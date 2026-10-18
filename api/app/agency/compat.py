"""Event names the agency recognises from other branches (A21, D13), by name only (A22).

The agency is preparation only (ADR-033): it reads these events when they are in a case's log and never writes
them, never calls the services that write them and never looks those services up. Nothing here is a hook.

* D13 (reply triage): `reply_received`, `reply_classified {intent, confidence, model_id, needs_human}`. A pending
  reply becomes an inbox item for a person; the agency sees its event id and, once classified, the labels.
* A21 (automation): automatic sends `message_sent {auto: true, template_id, purpose}`. Outreach does not draft a
  message for a purpose that an automatic template already covered on that case in the last 7 days.
  `closure_proposed {evidence_ref}` is noted in the case facts.
"""

from __future__ import annotations

REPLY_RECEIVED = "reply_received"
REPLY_CLASSIFIED = "reply_classified"
CLOSURE_PROPOSED = "closure_proposed"
AUTOMATION_ACTOR = "system:automation"
AUTO_TEMPLATE_WINDOW_DAYS = 7
