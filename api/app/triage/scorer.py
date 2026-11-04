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


class LlamaServerScorer:
    """The same nine-token next-token scoring as MlxScorer, against a llama.cpp `llama-server` (the CPU build of the SAME fine-tuned
    model, merged and converted to GGUF: tools/triage/convert_gguf.sh). Used by the hosted demo (deploy/gcp), where MLX cannot run.

    One request to `/apply-template` (the model's own chat template, so the prompt is token-for-token what the MLX path builds), one
    to `/completion` with `n_predict=1` and `n_probs` large enough that the nine label tokens are in the returned log-probabilities.
    A label token missing from them has a probability below the n_probs-th, so it gets a floor of -30 (nats), which changes
    nothing observable after the softmax. `logits` are the log-probabilities of the nine tokens; the temperature fitted on this
    build's validation split (calibration_gguf.json) is what turns them into a confidence, as for the MLX build. `mass` is the
    probability the model puts on the nine tokens.
    """

    FLOOR = -30.0

    def __init__(self, base_url: str, timeout: float = 20.0, n_probs: int = 200) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._n_probs = n_probs
        self._label_ids = [self._tokenize(label)[0] for label in INTENTS]
        if len(set(self._label_ids)) != len(INTENTS):
            raise RuntimeError("the intent labels do not start with distinct tokens for this tokenizer")

    def _post(self, path: str, body: dict) -> dict:
        import json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(self._base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - the operator's own llama-server
            return json.loads(resp.read().decode("utf-8"))

    def _tokenize(self, text: str) -> list[int]:
        return [int(t) for t in self._post("/tokenize", {"content": text, "add_special": False})["tokens"]]

    def score(self, text: str) -> dict:
        prompt = self._post("/apply-template", {"messages": [{"role": "user", "content": build_prompt(text[:MAX_TEXT_CHARS])}]})["prompt"]
        out = self._post("/completion", {"prompt": prompt, "n_predict": 1, "n_probs": self._n_probs, "temperature": 0.0,
                                         "cache_prompt": True, "stream": False})
        first = out["completion_probabilities"][0]
        tops = first.get("top_logprobs") or first.get("probs") or []
        by_id: dict[int, float] = {}
        for entry in tops:
            if "logprob" in entry:
                by_id[int(entry["id"])] = float(entry["logprob"])
            elif entry.get("prob") is not None:
                by_id[int(entry.get("id", -1))] = math.log(max(float(entry["prob"]), 1e-30))
        values = [by_id.get(i, self.FLOOR) for i in self._label_ids]
        mass = sum(math.exp(v) for v in values)
        return {"logits": values, "mass": min(mass, 1.0)}
