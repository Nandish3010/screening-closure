"""Storage for drafts, their prompts and abstentions (D4, D11).

`InMemoryDraftStore` backs unit tests and no-database runs; `PostgresDraftStore` writes the
`drafts` and `prompts` tables of migration 007_drafts. Both keep the same rows. A draft's
abstention (reason and time) lives on the draft row, so counting abstentions is counting rows.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID


class DraftNotFound(KeyError):
    pass


class DraftAlreadyDecided(ValueError):
    """The draft was already approved or rejected: a draft gets exactly one decision (D5)."""


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
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None

    @property
    def decided(self) -> bool:
        return self.approved_at is not None or self.rejected_at is not None


@dataclass(frozen=True)
class AttemptCount:
    """How many draft attempts for one language and channel ended with this abstention reason
    (None = the draft was not an abstention)."""

    language: str
    channel: str
    reason: str | None
    count: int


class DraftStore(Protocol):
    def add(self, draft: DraftRecord, prompt: PromptRecord, cur=None) -> None:
        """Insert the draft and its prompt. `cur`: the caller's Postgres cursor, so the rows commit (or
        roll back) with the `message_drafted` event written in the same transaction (SEC-2)."""
        ...

    def get(self, draft_id: UUID) -> DraftRecord | None: ...

    def list_for_case(self, case_id: UUID) -> list[DraftRecord]: ...

    def get_prompt(self, prompt_hash: str) -> PromptRecord | None: ...

    def attempt_counts(self) -> list[AttemptCount]:
        """Attempts grouped by (language, channel, abstention reason). The input to D11."""
        ...

    def approve(self, draft_id: UUID, approved_by: str, approved_at: datetime, cur=None) -> DraftRecord:
        """Record the one approval (`cur`: run inside the caller's Postgres transaction).
        Raises DraftNotFound / DraftAlreadyDecided."""
        ...

    def reject(self, draft_id: UUID, rejected_by: str, rejected_at: datetime, reason: str,
               cur=None) -> DraftRecord:
        """Record the one rejection. Raises DraftNotFound / DraftAlreadyDecided."""
        ...


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drafts: dict[UUID, DraftRecord] = {}
        self._prompts: dict[str, PromptRecord] = {}

    def add(self, draft: DraftRecord, prompt: PromptRecord, cur=None) -> None:
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

    def _decide(self, draft_id: UUID, **changes) -> DraftRecord:
        with self._lock:
            d = self._drafts.get(draft_id)
            if d is None:
                raise DraftNotFound(str(draft_id))
            if d.decided:
                raise DraftAlreadyDecided(f"draft {draft_id} already has a decision")
            self._drafts[draft_id] = replace(d, **changes)
            return self._drafts[draft_id]

    def approve(self, draft_id: UUID, approved_by: str, approved_at: datetime, cur=None) -> DraftRecord:
        return self._decide(draft_id, approved_at=approved_at, approved_by=approved_by)

    def reject(self, draft_id: UUID, rejected_by: str, rejected_at: datetime, reason: str,
               cur=None) -> DraftRecord:
        return self._decide(draft_id, rejected_at=rejected_at, rejected_by=rejected_by,
                            rejection_reason=reason)


_COLS = (
    "draft_id, case_id, purpose, channel, language, text, provider, model_id, fallback_used, "
    "abstention_reason, abstained_at, prompt_hash, created_by, created_at, approved_at, approved_by"
)
_ALL_COLS = _COLS + ", rejected_at, rejected_by, rejection_reason"


class PostgresDraftStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    def add(self, draft: DraftRecord, prompt: PromptRecord, cur=None) -> None:
        def write(execute) -> None:
            execute(
                "INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, %s, %s)"
                " ON CONFLICT (prompt_hash) DO NOTHING",
                (prompt.prompt_hash, prompt.system_prompt, prompt.prompt_text),
            )
            execute(
                f"INSERT INTO drafts ({_COLS}) VALUES ({', '.join(['%s'] * 16)})",
                (draft.draft_id, draft.case_id, draft.purpose, draft.channel, draft.language, draft.text,
                 draft.provider, draft.model_id, draft.fallback_used, draft.abstention_reason,
                 draft.abstained_at, draft.prompt_hash, draft.created_by, draft.created_at,
                 draft.approved_at, draft.approved_by),
            )

        if cur is not None:  # the caller's transaction (the event store's append)
            write(cur.execute)
            return
        with self.pool.connection() as conn, conn.transaction():
            write(conn.execute)

    def get(self, draft_id: UUID) -> DraftRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {_ALL_COLS} FROM drafts WHERE draft_id = %s", (draft_id,)).fetchone()
        return DraftRecord(*row) if row else None

    def list_for_case(self, case_id: UUID) -> list[DraftRecord]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_ALL_COLS} FROM drafts WHERE case_id = %s ORDER BY created_at, seq", (case_id,)
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

    # -- decisions (D5). `cur` lets the caller run the update inside its own transaction, so the
    # approval and the `message_approved` event commit or roll back together.

    def _decide(self, cur, sets: str, params: tuple, draft_id: UUID) -> DraftRecord:
        sql = (
            f"UPDATE drafts SET {sets} WHERE draft_id = %s"
            " AND approved_at IS NULL AND rejected_at IS NULL"
            f" RETURNING {_ALL_COLS}"
        )
        if cur is None:
            with self.pool.connection() as conn:
                row = conn.execute(sql, (*params, draft_id)).fetchone()
                existing = None if row else conn.execute(
                    "SELECT 1 FROM drafts WHERE draft_id = %s", (draft_id,)).fetchone()
        else:
            row = cur.execute(sql, (*params, draft_id)).fetchone()
            existing = None if row else cur.execute(
                "SELECT 1 FROM drafts WHERE draft_id = %s", (draft_id,)).fetchone()
        if row is None:
            if existing is None:
                raise DraftNotFound(str(draft_id))
            raise DraftAlreadyDecided(f"draft {draft_id} already has a decision")
        return DraftRecord(*row)

    def approve(self, draft_id: UUID, approved_by: str, approved_at: datetime, cur=None) -> DraftRecord:
        return self._decide(cur, "approved_at = %s, approved_by = %s", (approved_at, approved_by), draft_id)

    def reject(self, draft_id: UUID, rejected_by: str, rejected_at: datetime, reason: str,
               cur=None) -> DraftRecord:
        return self._decide(
            cur, "rejected_at = %s, rejected_by = %s, rejection_reason = %s",
            (rejected_at, rejected_by, reason), draft_id)
