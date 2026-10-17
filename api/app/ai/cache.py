"""Output cache for model providers: record once, replay offline (D6).

A model provider (`azure_openai`, `jais`) is wrapped in `CachingProvider`. The mode comes from
`LLM_CACHE_MODE` at call time:

* `replay` (default): never touches the network. A recorded output for the prompt is returned;
  with none, `CacheMiss` is raised and `draft_with_fallback` uses the template and counts the
  abstention as `cache_miss`.
* `record`: calls the real provider (which still needs `LLM_NETWORK_ENABLED=true`) and stores
  its output, whether or not the validator will accept it, so a replay reproduces the same
  outcome, abstentions included.
* `off`: no cache; the provider is called as is.

One JSON file per output: `<cache dir>/<provider>/<prompt_hash>.json`, holding the exact
prompt, so a reviewer can see what was asked. The default directory is `api/app/ai/cache/`
(committed for the demo); `LLM_CACHE_DIR` overrides it. The template provider needs no cache.
Files are written deterministically (sorted keys, no timestamps), so re-recording an
unchanged output leaves the file unchanged.

Record the demo outputs with real credentials on a machine that is allowed to call the model:

    LLM_PROVIDER=azure_openai LLM_NETWORK_ENABLED=true python -m app.ai.cache record
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from app.ai.provider import (
    CacheMiss,
    DraftRequest,
    DraftResult,
    Provider,
    full_prompt,
    prompt_hash,
)

log = logging.getLogger("app.ai.cache")

MODES = ("replay", "record", "off")
DEFAULT_MODE = "replay"
DEFAULT_CACHE_DIR = Path(__file__).parent / "cache"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def cache_mode() -> str:
    value = (os.environ.get("LLM_CACHE_MODE") or DEFAULT_MODE).strip().lower()
    if value not in MODES:
        raise ValueError(f"LLM_CACHE_MODE must be one of {MODES}, got {value!r}")
    return value


def cache_dir() -> Path:
    override = os.environ.get("LLM_CACHE_DIR")
    return Path(override) if override and override.strip() else DEFAULT_CACHE_DIR


@dataclass(frozen=True)
class CacheEntry:
    prompt_hash: str
    provider: str
    model_id: str
    language: str
    text: str
    prompt: str  # the exact text the hash covers (system message, separator, user message)


def _path(provider: str, prompt_hash_: str, directory: Path | None = None) -> Path:
    if not _NAME_RE.match(provider) or not _HASH_RE.match(prompt_hash_):
        raise ValueError("bad cache key")
    return (directory or cache_dir()) / provider / f"{prompt_hash_}.json"


def read_entry(provider: str, prompt_hash_: str, directory: Path | None = None) -> CacheEntry | None:
    path = _path(provider, prompt_hash_, directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = CacheEntry(**data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        log.warning("unreadable cache entry %s (%s): treated as a miss", path.name, type(exc).__name__)
        return None
    if entry.prompt_hash != prompt_hash_ or entry.provider != provider:
        log.warning("cache entry %s does not match its key: treated as a miss", path.name)
        return None
    return entry


def write_entry(entry: CacheEntry, directory: Path | None = None) -> Path:
    path = _path(entry.provider, entry.prompt_hash, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


class CachingProvider:
    """Wraps a model provider with record/replay. Exposes the inner provider's name and model."""

    def __init__(self, inner: Provider, mode: str | None = None) -> None:
        if mode is not None and mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode is None:
            cache_mode()  # fail fast on a bad LLM_CACHE_MODE, when the provider is resolved
        self._inner = inner
        self._mode = mode
        self.name = inner.name

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def draft(self, req: DraftRequest) -> DraftResult:
        mode = self._mode or cache_mode()
        if mode == "off":
            return self._inner.draft(req)
        key = prompt_hash(req)
        if mode == "replay":
            entry = read_entry(self.name, key)
            if entry is None:
                raise CacheMiss(f"no recorded output for {self.name} prompt {key[:12]}")
            return DraftResult(entry.text, req.language, self.name, entry.model_id, False, None, key)
        result = self._inner.draft(req)  # record: the real call (needs LLM_NETWORK_ENABLED)
        write_entry(CacheEntry(key, self.name, result.model_id, req.language, result.text, full_prompt(req)))
        return result


# ---------------------------------------------------------------- recording CLI


def _demo_requests(owner_name: str, owner_role: str):
    """The full purpose x channel x language matrix for one care owner (appointment date included
    for appointment confirmations)."""
    from app.ai.provider import CHANNELS, LANGUAGES, PURPOSES
    from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES

    for language in LANGUAGES:
        for purpose in PURPOSES:
            for channel in CHANNELS:
                date = SAMPLE_VALUES[language]["appointment_date"] if purpose == "appointment_confirmation" else None
                yield DraftRequest(purpose, channel, language, owner_name, owner_role,
                                   DEFAULT_NEXT_STEP[language], "colorectal_fit", date)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.ai.provider import configured_provider_name, get_provider

    parser = argparse.ArgumentParser(prog="python -m app.ai.cache")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record model outputs for the purpose x channel x language matrix")
    rec.add_argument("--owner-name", default="Dr Sara Khan")
    rec.add_argument("--owner-role", default="primary_care_owner")
    sub.add_parser("list", help="count recorded outputs per provider")
    args = parser.parse_args(argv)

    if args.command == "list":
        for d in sorted(p for p in cache_dir().iterdir() if p.is_dir()):
            print(f"{d.name}: {len(list(d.glob('*.json')))} outputs")
        return 0

    name = configured_provider_name()
    if name == "template":
        print("LLM_PROVIDER is template: nothing to record")
        return 1
    os.environ["LLM_CACHE_MODE"] = "record"
    provider = get_provider(name)
    done = failed = 0
    for req in _demo_requests(args.owner_name, args.owner_role):
        try:
            provider.draft(req)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAILED {req.language}/{req.purpose}/{req.channel}: {type(exc).__name__}: {exc}")
    print(f"recorded {done}, failed {failed}, into {cache_dir()}")
    return 0 if not failed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
