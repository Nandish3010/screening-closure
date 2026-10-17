#!/usr/bin/env python3
"""Secrets and repo hygiene scan (F9, T-F12).

What it checks, in order:

1. `.env` hygiene: `.env` is git-ignored, not tracked, and no path named `.env` (or `.env.<x>`
   other than `.env.example`) has ever been in history on any ref (`git log --all`).
2. Tracked files (plus untracked-but-not-ignored files): token shapes for AWS, Azure, OpenAI and
   Anthropic, GitHub, Slack, Google; PEM private keys; JWT-looking strings (the Hack2skill tokens
   must not exist anywhere in the repo); bearer-token literals; `password=`-style literals and
   credentials embedded in URLs.
   Exempt from the password/URL-credential rules only (never from token shapes): `.env.example`,
   test files and test directories, and docker-compose defaults of the form `${VAR:-default}`.
3. `.env.example` holds placeholders only: every secret-named key is empty, a `change-me` /
   `<...>` / `your-...` placeholder, or the documented local dev default.
4. History: every added line, removed line and commit message on every ref goes through the same
   token-shape rules (with the file's path, so the password exemptions still apply).

If `gitleaks` is on PATH it is also run (`gitleaks detect --no-banner --redact`) as a second
opinion; it is optional because the regex scanner above is the gate. Findings never print the
secret: only a 4-character prefix and the length.

Silence a reviewed false positive with the marker `secrets-scan: allow` on the same line.

Usage: python tools/check/secrets_scan.py [--root DIR] [--no-history] [--no-gitleaks]
Exit code 0 = clean, 1 = findings.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ALLOW_MARKER = "secrets-scan: allow"
MAX_FILE_BYTES = 2_000_000
# The one password the repo documents as a local-only placeholder (.env.example, compose default).
LOCAL_DEV_DEFAULTS = {"closure_dev_password"}

# --- token shapes: apply to every file, in the tree and in history -------------------------------
TOKEN_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b")),
    ("aws-secret-access-key", re.compile(
        r"(?i)aws[_-]?secret[_-]?(?:access[_-]?)?key[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}\b")),
    ("azure-storage-key", re.compile(r"AccountKey=[A-Za-z0-9+/]{40,}={0,2}")),
    ("azure-sas-signature", re.compile(r"(?i)[?&]sig=[A-Za-z0-9%]{43,}")),
    ("azure-ad-client-secret", re.compile(r"\b[A-Za-z0-9_.~-]{3}\dQ~[A-Za-z0-9_.~-]{30,}")),
    ("azure-openai-key", re.compile(
        r"(?i)(?:azure[_-]?openai[_-]?(?:api[_-]?)?key|ocp-apim-subscription-key|api-key)[\"']?\s*[=:]\s*[\"']?[0-9a-f]{32}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")),
    ("jwt-fragment", re.compile(r"\beyJ[A-Za-z0-9_-]{24,}")),
    ("bearer-literal", re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?bearer\s+(?![\$\{<%])[A-Za-z0-9._~+/=-]{24,}")),
]

# --- literal credentials: skipped for exempt paths ----------------------------------------------
KEYISH = r"[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)[\w.-]*"
CRED_ASSIGN_RE = re.compile(
    r"(?i)\b(?P<key>" + KEYISH + r")[\"']?\s*(?:=|:=|:)\s*(?:\"(?P<dq>[^\"\n]*)\"|'(?P<sq>[^'\n]*)'|(?P<bare>[^\s\"'#,;)\]}]+))"
)
URL_CRED_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@\"'<>]+:(?P<pw>[^\s@/\"'<>]+)@")
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:\$|%|<|dev-only-|[\^~>=<v]*\d+(?:\.[\dx*]+)*(?:[-+][\w.]+)?$|\{\{|\.\.\.|\*+$|x{3,}|change[-_ ]?me|changeme|placeholder|example|your[-_ ]|dummy|redacted|"
    r"none$|null$|true$|false$|required$|optional$|secret$|password$|token$|todo|tbd|\s*$)")
CODE_REF_RE = re.compile(
    r"^(?:os\.|self\.|settings\.|config\.|cfg\.|env\.|process\.env|import\.meta|getenv|environ|args\.|opts\.|"
    r"req\.|request\.|headers|params|data\.|secrets\.|[A-Za-z_][\w.]*\(|\[)")
TEST_PATH_PATTERNS = ["tests/*", "*/tests/*", "*/test/*", "*/e2e/*", "*/__tests__/*", "*/test_*.py", "test_*.py",
                      "*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx", "*/conftest.py", "conftest.py"]
CRED_EXEMPT_PATTERNS = ["package-lock.json", "*/package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".env.example", "*/.env.example", "docker-compose*.yml", "docker-compose*.yaml",
                        "compose*.yml", *TEST_PATH_PATTERNS]
SECRET_NAMED_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)")
ENV_FILE_RE = re.compile(r"(^|/)\.env(\.(?!example$)[^/]+)?$")
KEY_FILE_PATTERNS = ["*.pem", "*.p12", "*.pfx", "*.key", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
                     "*.jks", "*.keystore"]
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".otf",
                 ".zip", ".gz", ".jar", ".mp4", ".mov", ".lock"}


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str  # path:line, or hist:<sha>:path:line
    snippet: str  # redacted

    def __str__(self) -> str:
        return f"{self.where}: [{self.rule}] {self.snippet}"


def redact(value: str) -> str:
    value = value.strip()
    return f"{value[:4]}... ({len(value)} chars)"


def matches(path: str, patterns: Iterable[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def is_placeholder(value: str) -> bool:
    v = value.strip()
    if len(v) < 6 or PLACEHOLDER_RE.match(v) or CODE_REF_RE.match(v):
        return True
    if v in LOCAL_DEV_DEFAULTS:
        return True
    # a value made only of letters, dots and dashes with no digit reads as a word or identifier,
    # not a generated secret (type names, header names, translation keys)
    return not re.search(r"\d", v) and not re.search(r"[!@#$%^&*+/=~]", v) and len(v) < 24 and "-" not in v[8:]


def scan_line(path: str, line: str, *, exempt_creds: bool | None = None) -> list[tuple[str, str]]:
    """Return [(rule, redacted snippet)] for one line of one file."""
    if ALLOW_MARKER in line:
        return []
    found: list[tuple[str, str]] = []
    for rule, rx in TOKEN_RULES:
        for m in rx.finditer(line):
            if rule == "jwt-fragment" and any(r == "jwt" for r, _ in found):
                continue
            found.append((rule, redact(m.group(0))))
    if exempt_creds is None:
        exempt_creds = matches(path, CRED_EXEMPT_PATTERNS)
    if not exempt_creds:
        for m in CRED_ASSIGN_RE.finditer(line):
            value = next((m.group(g) for g in ("dq", "sq", "bare") if m.group(g) is not None), "")
            if value is not None and not is_placeholder(value):
                found.append(("credential-literal", f"{m.group('key')}={redact(value)}"))
        for m in URL_CRED_RE.finditer(line):
            if not is_placeholder(m.group("pw")):
                found.append(("url-credentials", redact(m.group("pw"))))
    return found


def run_git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=text)


def list_files(root: Path) -> list[str]:
    out = run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", text=False)
    if out.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {out.stderr.decode(errors='replace')}")
    return sorted({p for p in out.stdout.decode().split("\0") if p})


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for rel in list_files(root):
        path = root / rel
        if path.is_symlink() or not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if matches(rel, KEY_FILE_PATTERNS):
            findings.append(Finding("key-file", rel, "private key / keystore file is tracked"))
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue
        for no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            for rule, snip in scan_line(rel, line):
                findings.append(Finding(rule, f"{rel}:{no}", snip))
    return findings


def check_env_hygiene(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    ignored = run_git(root, "check-ignore", "-q", ".env")
    if ignored.returncode != 0:
        findings.append(Finding("env-not-ignored", ".gitignore", ".env is not git-ignored"))
    tracked = run_git(root, "ls-files", "--", ".env").stdout.split()
    if tracked:
        findings.append(Finding("env-tracked", ".env", ".env is tracked"))
    hist = run_git(root, "log", "--all", "--name-only", "--format=", "--diff-filter=ACMRTD").stdout.splitlines()
    for p in sorted({h for h in hist if ENV_FILE_RE.search(h)}):
        findings.append(Finding("env-in-history", p, f"{p} appears in git history (git log --all -- {p})"))
    return findings


def check_env_example(root: Path) -> list[Finding]:
    path = root / ".env.example"
    if not path.is_file():
        return [Finding("env-example-missing", ".env.example", "file not found")]
    findings = []
    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if not m or not SECRET_NAMED_RE.search(m.group(1)):
            continue
        value = m.group(2).strip().strip("\"'")
        if value and not is_placeholder(value) and value not in LOCAL_DEV_DEFAULTS:
            findings.append(Finding("env-example-real-value", f".env.example:{no}", f"{m.group(1)}={redact(value)}"))
    return findings


def history_lines(root: Path) -> Iterator[tuple[str, str, str]]:
    """Yield (sha, path, line) for every line of every patch and commit message on every ref."""
    proc = subprocess.Popen(
        ["git", "-C", str(root), "log", "--all", "-p", "--no-color", "-U0", "--format=commit %H%n%B", "--no-ext-diff"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    sha, path = "", ""
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line.startswith("commit ") and re.fullmatch(r"commit [0-9a-f]{40}", line):
            sha, path = line[7:], "(commit message)"
            continue
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/") if line[4:] != "/dev/null" else path
            continue
        if line.startswith("--- "):
            path = line[4:].removeprefix("a/") if line[4:] != "/dev/null" else path
            continue
        if line.startswith("diff --git "):
            path = "(diff)"
            continue
        if line.startswith("@@"):
            continue
        if path == "(commit message)":
            yield sha, path, line
        elif line[:1] in "+-":
            yield sha, path, line[1:]
    proc.wait()


def scan_history(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for sha, path, line in history_lines(root):
        for rule, snip in scan_line(path, line):
            key = (rule, path, snip)
            if key in seen:  # the same secret re-added across commits is one finding
                continue
            seen.add(key)
            findings.append(Finding(rule, f"history:{sha[:10]}:{path}", snip))
    return findings


def run_gitleaks(root: Path) -> list[Finding]:
    exe = shutil.which("gitleaks")
    if not exe:
        return []
    proc = subprocess.run([exe, "detect", "--no-banner", "--redact", "--source", str(root), "--exit-code", "1"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return []
    return [Finding("gitleaks", "gitleaks", (proc.stdout + proc.stderr).strip()[:400])]


def scan(root: Path, history: bool = True, gitleaks: bool = True) -> tuple[list[Finding], dict[str, object]]:
    findings = check_env_hygiene(root) + check_env_example(root) + scan_tree(root)
    stats: dict[str, object] = {"files": len(list_files(root))}
    if history:
        findings += scan_history(root)
        stats["commits"] = len(run_git(root, "rev-list", "--all").stdout.split())
    if gitleaks:
        gl = run_gitleaks(root)
        findings += gl
        stats["gitleaks"] = "ran" if shutil.which("gitleaks") else "not installed (regex scanner only)"
    return findings, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="git repository to scan (default: two levels above this file)")
    ap.add_argument("--no-history", action="store_true", help="skip the history scan")
    ap.add_argument("--no-gitleaks", action="store_true", help="do not run gitleaks even if installed")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    findings, stats = scan(root, history=not args.no_history, gitleaks=not args.no_gitleaks)
    for f in findings:
        print(f"FINDING {f}")
    extra = ", ".join(f"{k}={v}" for k, v in stats.items())
    print(f"secrets-scan: {'FAIL' if findings else 'PASS'} ({len(findings)} finding(s); {extra})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
