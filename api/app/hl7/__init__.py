"""HL7 v2 adapter at the integration boundary (A28, M6, ADR-039).

The engine is FHIR R4 inside and knows nothing about HL7 (ADR-008). This package is the one place that does: a small
parser (`parser`), the field mappings and code sets (`mapping`), the handler that turns one message into at most one
call of an existing path and an ACK (`handler`), and an optional MLLP listener (`mllp`) that hands every message to the
same handler through `POST /hl7`. Nothing here is connected to Malaffi; see docs/governance/hl7-adapter.md.
"""
