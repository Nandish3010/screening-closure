"""Storage for drafts, their prompts and abstentions (D4, D11).

`InMemoryDraftStore` backs unit tests and no-database runs; `PostgresDraftStore` writes the
`drafts` and `prompts` tables of migration 002_drafts. Both keep the same rows. A draft's
abstention (reason and time) lives on the draft row, so counting abstentions is counting rows.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PromptRecord:
    prompt_hash: str
    system_prompt: str
    prompt_text: str


@dataclass(frozen=True)
class DraftRecord:
    draft_id: UUID
    case_id: UUID
    purpose: str
    channel: str
    language: str
    text: str
    provider: str
    model_id: str
    fallback_used: bool
    abstention_reason: str | None
    abstained_at: datetime | None
    prompt_hash: str
    created_by: str
    created_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None


@dataclass(frozen=True)
class AttemptCount:
    """How many draft attempts for one language and channel ended with this abstention reason
    (None = the draft was not an abstention)."""

    language: str
    channel: str
    reason: str | None
    count: int


class DraftStore(Protocol):
    def add(self, draft: DraftRecord, prompt: PromptRecord) -> None: ...

    def get(self, draft_id: UUID) -> DraftRecord | None: ...

    def list_for_case(self, case_id: UUID) -> list[DraftRecord]: ...

    def get_prompt(self, prompt_hash: str) -> PromptRecord | None: ...

    def attempt_counts(self) -> list[AttemptCount]:
        """Attempts grouped by (language, channel, abstention reason). The input to D11."""
        ...


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drafts: dict[UUID, DraftRecord] = {}
        self._prompts: dict[str, PromptRecord] = {}

    def add(self, draft: DraftRecord, prompt: PromptRecord) -> None:
        with self._lock:
            if draft.draft_id in self._drafts:
                raise ValueError(f"draft {draft.draft_id} already exists")
            self._prompts.setdefault(prompt.prompt_hash, prompt)
            self._drafts[draft.draft_id] = draft

    def get(self, draft_id: UUID) -> DraftRecord | None:
        with self._lock:
            return self._drafts.get(draft_id)

    def list_for_case(self, case_id: UUID) -> list[DraftRecord]:
        with self._lock:
            rows = [d for d in self._drafts.values() if d.case_id == case_id]
        return sorted(rows, key=lambda d: d.created_at)  # stable: insertion order within a tick

    def get_prompt(self, prompt_hash: str) -> PromptRecord | None:
        with self._lock:
            return self._prompts.get(prompt_hash)

    def attempt_counts(self) -> list[AttemptCount]:
        with self._lock:
            counter = Counter((d.language, d.channel, d.abstention_reason) for d in self._drafts.values())
        return [AttemptCount(*key, n) for key, n in sorted(counter.items(), key=lambda kv: tuple(map(str, kv[0])))]

    def approve(self, draft_id: UUID, approved_by: str, approved_at: datetime) -> DraftRecord:
        """Used by D5 and by tests of the immutability rule: only the approval fields change, once."""
        with self._lock:
            d = self._drafts[draft_id]
            if d.approved_at is not None:
                raise ValueError(f"draft {draft_id} is already approved")
            self._drafts[draft_id] = replace(d, approved_at=approved_at, approved_by=approved_by)
            return self._drafts[draft_id]


_COLS = (
    "draft_id, case_id, purpose, channel, language, text, provider, model_id, fallback_used, "
    "abstention_reason, abstained_at, prompt_hash, created_by, created_at, approved_at, approved_by"
)


class PostgresDraftStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    def add(self, draft: DraftRecord, prompt: PromptRecord) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, %s, %s)"
                " ON CONFLICT (prompt_hash) DO NOTHING",
                (prompt.prompt_hash, prompt.system_prompt, prompt.prompt_text),
            )
            conn.execute(
                f"INSERT INTO drafts ({_COLS}) VALUES ({', '.join(['%s'] * 16)})",
                (draft.draft_id, draft.case_id, draft.purpose, draft.channel, draft.language, draft.text,
                 draft.provider, draft.model_id, draft.fallback_used, draft.abstention_reason,
                 draft.abstained_at, draft.prompt_hash, draft.created_by, draft.created_at,
                 draft.approved_at, draft.approved_by),
            )

    def get(self, draft_id: UUID) -> DraftRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {_COLS} FROM drafts WHERE draft_id = %s", (draft_id,)).fetchone()
        return DraftRecord(*row) if row else None

    def list_for_case(self, case_id: UUID) -> list[DraftRecord]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM drafts WHERE case_id = %s ORDER BY created_at, seq", (case_id,)
            ).fetchall()
        return [DraftRecord(*r) for r in rows]

    def get_prompt(self, prompt_hash: str) -> PromptRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT prompt_hash, system_prompt, prompt_text FROM prompts WHERE prompt_hash = %s",
                (prompt_hash,),
            ).fetchone()
        return PromptRecord(*row) if row else None

    def attempt_counts(self) -> list[AttemptCount]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT language, channel, abstention_reason, count(*) FROM drafts"
                " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
            ).fetchall()
        return [AttemptCount(*r) for r in rows]
