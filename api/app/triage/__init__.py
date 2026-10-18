"""Reply triage (D13): classify an inbound patient reply and route the uncertain ones to a human.

`classify.classify(text, language_hint)` is the only entry point the API uses. Everything that needs MLX lives
behind it (`scorer.py`, `worker.py`), so importing this package never imports a model library.
"""
