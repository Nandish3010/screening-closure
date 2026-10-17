# Design choices

## Deterministic engine with human gates

Safety-critical state transitions are deterministic and replayable: every change to a case is an appended event, state is derived by replaying those events, and the same events always produce the same state, which can be explained line by line to a reviewer. A language model may draft a message or summarise a document, but it never moves a case between states or decides a closure; the model drafts, humans decide, and the architecture makes anything else structurally impossible rather than merely against policy.

## Comparison

| Dimension | Deterministic engine + human gates | Agentic orchestration |
|---|---|---|
| Auditability | TODO | TODO |
| Replayability | TODO | TODO |
| Latency | TODO | TODO |
| Cost per case | TODO | TODO |
| Failure modes | TODO | TODO |
| Model unavailable | TODO | TODO |
