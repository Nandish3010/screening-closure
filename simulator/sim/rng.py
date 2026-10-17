"""Seed derivation. Every random stream is seeded from a uuid5 of (base seed, labels...), so a
stream depends only on its own labels: adding patients or streams never shifts other streams."""

from __future__ import annotations

import random
import uuid

NAMESPACE = uuid.UUID("0b6a3a1e-5f5e-4b4a-9a5b-6c1d7e2f8a90")  # never change: it would re-seed everything


def derive_seed(*parts) -> int:
    return uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts)).int & ((1 << 64) - 1)


def stream(*parts) -> random.Random:
    return random.Random(derive_seed(*parts))


def derive_uuid(*parts) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts))
