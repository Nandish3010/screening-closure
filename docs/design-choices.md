# Design choices

## Deterministic engine with human gates

Safety-critical state transitions are deterministic and replayable: every change to a case is an appended event, state is derived by replaying those events, and the same events always produce the same state, which can be explained line by line to a reviewer. A language model may draft a message or summarise a document, but it never moves a case between states or decides a closure; the model drafts, humans decide, and the architecture makes anything else structurally impossible rather than merely against policy.

## Transition check inside the database transaction

- The state-machine check runs inside the same transaction that locks the case row (`SELECT ... FOR UPDATE`) and inserts the event, because checking in the API process and then writing would let two concurrent requests both validate against the same old state and both commit; holding the lock makes validate-then-append atomic, so the closure invariant (evidence and a human sign-off) holds under concurrency and retries, not just in single-threaded tests.

## Comparison

| Dimension | Deterministic engine + human gates | Agentic orchestration |
|---|---|---|
| Auditability | TODO | TODO |
| Replayability | TODO | TODO |
| Latency | TODO | TODO |
| Cost per case | TODO | TODO |
| Failure modes | TODO | TODO |
| Model unavailable | TODO | TODO |
