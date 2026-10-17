"""Abstention accounting (D11).

The automation abstention rate is the share of draft attempts in which the system did not use
the model's output (docs/governance/ai-boundary.md section 6):

    rate = abstentions / draft attempts

Every `POST /cases/{id}/drafts` is one attempt and one `drafts` row. A row whose
`abstention_reason` is set is an abstention (the template was used). So the counts come
straight from the stored rows and survive a restart, unlike the in-memory `AbstentionLog`.

Two entry points:
* `summarise` backs `GET /metrics/abstention`.
* `abstention_rate_hook` is what the simulator's `metrics.AbstentionHook` calls.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from app.ai.draft_store import AttemptCount

# The simulator's preferred contact channel "phone" has no written draft; the written
# follow-up it would get is an SMS. Languages other than ar/en are drafted in English.
_SIM_CHANNEL = {"phone": "sms"}


def _rate(abstentions: int, attempts: int) -> float:
    return round(abstentions / attempts, 6) if attempts else 0.0


def category(reason: str) -> str:
    """`validation_failed:forbidden_phrase:cancer` -> `validation_failed`."""
    return reason.split(":", 1)[0]


def summarise(counts: Iterable[AttemptCount]) -> dict:
    """`{attempts, abstentions, rate, by_reason, by_language, by_channel}` from grouped counts.

    `by_reason` counts abstentions by reason category (`validation_failed`, `provider_unavailable`,
    `provider_error`, `invalid_result`, `cache_miss`). `by_language` and `by_channel` are the
    fairness split: attempts, abstentions and rate for each value.
    """
    attempts = abstentions = 0
    by_reason: Counter = Counter()
    split: dict[str, dict[str, list[int]]] = {"language": {}, "channel": {}}
    for c in counts:
        attempts += c.count
        is_abstention = c.reason is not None
        if is_abstention:
            abstentions += c.count
            by_reason[category(c.reason)] += c.count
        for dim, key in (("language", c.language), ("channel", c.channel)):
            cell = split[dim].setdefault(key, [0, 0])
            cell[0] += c.count
            cell[1] += c.count if is_abstention else 0

    def table(dim: str) -> dict:
        return {
            k: {"attempts": a, "abstentions": b, "rate": _rate(b, a)}
            for k, (a, b) in sorted(split[dim].items())
        }

    return {
        "attempts": attempts,
        "abstentions": abstentions,
        "rate": _rate(abstentions, attempts),
        "by_reason": dict(sorted(by_reason.items())),
        "by_language": table("language"),
        "by_channel": table("channel"),
    }


def abstention_rate_hook(records: Sequence) -> float | None:
    """Simulator hook (`sim.metrics.AbstentionHook`): the abstention rate for these cases.

    Each case record stands for one draft attempt in its own language and channel. The attempt
    runs through the real drafting pipeline (`draft_with_fallback` with the configured
    provider, offline) on a fixed sample request, with a private log, so nothing is stored.
    The result is therefore the rate the configured provider and cache would have for this
    language and channel mix: 0.0 for the `template` provider, 1.0 for a model provider with no
    recorded outputs (the model route is never used). Returns None for an empty list.
    """
    if not records:
        return None
    from app.ai.provider import CHANNELS, LANGUAGES, DraftRequest, get_provider
    from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES
    from app.ai.validate import AbstentionLog, draft_with_fallback

    def sample_key(record) -> tuple[str, str]:
        channel = _SIM_CHANNEL.get(record.channel, record.channel)
        return (
            record.language if record.language in LANGUAGES else "en",
            channel if channel in CHANNELS else "sms",
        )

    mix = Counter(sample_key(r) for r in records)
    provider = get_provider()
    abstentions = 0
    for (language, channel), n in mix.items():
        log = AbstentionLog()
        draft_with_fallback(
            DraftRequest(
                "result_notification", channel, language, SAMPLE_VALUES[language]["owner_name"],
                "primary_care_owner", DEFAULT_NEXT_STEP[language], "colorectal_fit",
            ),
            provider,
            log,
        )
        abstentions += n * log.count
    return abstentions / len(records)
