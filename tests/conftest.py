"""Shared fixtures for the cross-cutting tests (Cut F). Run with `make test-tools`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in ("api", "tools/check", "tools/load"):
    p = str(REPO_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


class TempRepo:
    """A throwaway git repository for scanner and lint tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.write(".env.example", "# placeholders only\nAPP_ENV=dev\n")

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=GIT_ENV)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def commit(self, message: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> TempRepo:
    return TempRepo(tmp_path / "repo")
