"""The intent labels, the routing rule between them, and the prompt format. Standard library only.

This file is shared by the API (serving) and by tools/triage (training and evaluation), so that the two cannot
disagree about the prompt the model was fine-tuned on or about which intents a person must always see.
"""

from __future__ import annotations

INTENTS = (
    "confirm",
    "reschedule",
    "cannot_attend",
    "wrong_number",
    "dont_understand",
    "opt_out",
    "question",
    "already_done",
    "other",
)

# Intents that are NEVER handled without a person, whatever the confidence:
#   question  - may be clinical ("is the result serious?"); an answer is a clinical act
#   other     - by definition not understood
#   opt_out   - a consent decision; a misread opt-out is the costliest error (docs/governance/hazard-log.md HZ-25)
ALWAYS_HUMAN = frozenset({"question", "other", "opt_out"})
# Intents that may trigger a safe automatic effect when the model is confident (see api/app/routers/replies.py).
AUTO_HANDLED = frozenset(INTENTS) - ALWAYS_HUMAN

PROMPT_PREFIX = "Classify the patient reply intent: "
MAX_TEXT_CHARS = 600  # longer replies are cut for the model (and always go to a person: see classify.py)


def build_prompt(text: str) -> str:
    """The user turn the model was fine-tuned on: the instruction and the reply, nothing else."""
    return f"{PROMPT_PREFIX}{text}"
