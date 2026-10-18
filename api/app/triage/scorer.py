"""Score a reply with the fine-tuned MLX model: one forward pass, the logits of the nine labels' first tokens.

The nine labels start with nine different tokens (checked at load time), so the next-token distribution at the
answer position, restricted to those nine tokens, is a distribution over the intents. That restricted softmax is
what `classify.py` calibrates with a temperature and compares to the abstain threshold.

Imports mlx lazily and only inside the class, because the API's own virtualenv does not have it: the model runs in
`tools/triage/.venv`, in a worker process (`worker.py`) when the API cannot import mlx itself.
"""

from __future__ import annotations

import math

from .labels import INTENTS, MAX_TEXT_CHARS, build_prompt


class MlxScorer:
    def __init__(self, model_path: str, adapter_path: str | None) -> None:
        from mlx_lm import load  # noqa: PLC0415 - lazy: not installed in the API virtualenv

        self._model, self._tok = load(model_path, adapter_path=adapter_path)
        self._label_ids = [self._tok.encode(label)[0] for label in INTENTS]
        if len(set(self._label_ids)) != len(INTENTS):
            raise RuntimeError("the intent labels do not start with distinct tokens for this tokenizer")

    def score(self, text: str) -> dict:
        """{"logits": [9 floats in INTENTS order], "mass": probability the model puts on the nine tokens}."""
        import mlx.core as mx  # noqa: PLC0415

        messages = [{"role": "user", "content": build_prompt(text[:MAX_TEXT_CHARS])}]
        ids = self._tok.apply_chat_template(messages, add_generation_prompt=True, return_dict=False)
        logits = self._model(mx.array([ids]))[0, -1, :].astype(mx.float32)
        log_norm = mx.logsumexp(logits)
        picked = logits[mx.array(self._label_ids)]
        mx.eval(picked, log_norm)
        values = [float(v) for v in picked.tolist()]
        mass = sum(math.exp(v - float(log_norm)) for v in values)
        return {"logits": values, "mass": mass}
