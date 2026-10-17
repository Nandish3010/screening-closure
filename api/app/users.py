"""User directory (A14, ADR-019): opaque `user_id` -> role, display name, provider.

The event log never holds a person's name: an actor is `role:user_id` with an opaque id from
`app.auth.opaque_user_id`. This directory is where the display name lives, so the UI can show
"Dr K" and an erasure request deletes a row instead of rewriting an append-only log.
Two implementations: in memory (unit tests, no-docker runs) and the Postgres `users` table
(migration 004), bound by `app.main` when DATABASE_URL is set.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    role: str
    display_name: str
    provider: str | None = None


class UserDirectory(Protocol):
    def upsert(self, record: UserRecord) -> None: ...

    def get(self, user_id: str) -> UserRecord | None: ...

    def ensure(self, identity) -> None:
        """Record the identity if its row is missing (a valid token must always resolve)."""
        ...


class InMemoryUserDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, UserRecord] = {}

    def upsert(self, record: UserRecord) -> None:
        with self._lock:
            self._rows[record.user_id] = record

    def get(self, user_id: str) -> UserRecord | None:
        with self._lock:
            return self._rows.get(user_id)

    def ensure(self, identity) -> None:
        if self.get(identity.user_id) is None:
            self.upsert(record_for(identity))


# Bound by app.main at start-up to the Postgres directory when DATABASE_URL is set.
directory: UserDirectory = InMemoryUserDirectory()


def record_for(identity) -> UserRecord:
    return UserRecord(identity.user_id, identity.role, identity.name, identity.provider)


def record_login(identity) -> UserRecord:
    """Write (or refresh) the directory row for a user who just logged in."""
    record = record_for(identity)
    directory.upsert(record)
    return record


def split_actor(actor: str) -> tuple[str, str]:
    """`role:user_id` -> (role, user_id); an actor without a colon is (actor, actor)."""
    role, _, user_id = actor.partition(":")
    return (role, user_id) if user_id else (actor, actor)


def resolve_actor(actor: str | None) -> dict | None:
    """`{actor, display_name, role}` for the UI, or None for no actor.

    Services and automated actors (`system:scheduler`) are their own display name. A user
    id with no directory row (an actor written before the directory existed, or after a
    reset) falls back to the id, so the UI always has something to show.
    """
    if not actor:
        return None
    role, user_id = split_actor(actor)
    if role in ("system", "scheduler", "watchdog", "model"):
        return {"actor": actor, "display_name": user_id, "role": role}
    rec = directory.get(user_id)
    return {"actor": actor, "display_name": rec.display_name if rec else user_id, "role": role}
