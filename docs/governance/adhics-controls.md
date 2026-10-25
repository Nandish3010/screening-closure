# ADHICS control-by-control table

Status: **draft for human review (E18, T-E21).** A design-time mapping of the Abu Dhabi Healthcare Information and Cyber Security Standard (ADHICS) to what this system does. It is not an audit, not a certification and not a compliance claim. The prototype holds synthetic data only ([data-provenance](../data-provenance.md)); most rows describe what a pilot deployment would need, and say what the repository has today. Related: [regulatory-mapping.md](regulatory-mapping.md) section 3 (the reading of ADHICS), [dpia.md](dpia.md), [hazard-log.md](hazard-log.md), [pilot-hardening.md](pilot-hardening.md), [identity.md](identity.md), [operations.md](operations.md), [erasure.md](erasure.md).

## 1. Which version, and what was actually read

| Version | Reference | What was read | Reliability |
|---|---|---|---|
| **v0.9** (3 February 2019) | DOH/SD/ADHICS/0.9, 116 pages | Read in full in an earlier session (regulatory-mapping.md section 3, access date 2026-10-18); the control that matters most, **CM 4.2** (category Basic, cloud use), was read in the text | Primary, but superseded |
| **V2** (published May 2024, effective August 2024) | DOH/SD/ICSO/ADHICS/V2/2024 | **The V2 text could not be retrieved on 2026-10-25.** The DoH host (doh.gov.ae) failed TLS verification from the fetch tool for the V2 PDF, the v0.9 PDF and the FAQ, and nothing was downloaded around that failure. A search result shows the V2 document's reference, dates and a statement that it replaces v0.9 and the 2020 IoMT and patient data privacy standards | Search-result metadata only |
| V2 secondary material | Vendor and consultancy pages (Qualysec, DeepStrike, Sehanexis, K7; all 2026-10-25) | Domain lists, "692 controls (162 primary, 530 secondary) in 11 domains" (attributed to the DoH FAQ in search results, not read), a "service provider" category for healthcare technology and services providers, three tiers (Basic, Transitional, Advanced) | **Secondary and inconsistent** (one vendor says 19 domains, another 12). Not used as evidence for any row |

Consequences for this table.

1. **Control ids.** Only `CM 4.2` is cited by id, because it was read in the v0.9 text. Every other row names a **control area** and carries "id to verify". Do not cite an id for these rows until the control list is read from the official PDF. Inventing ids would look precise and be wrong.
2. **Requirement summaries** are written from the control areas listed in regulatory-mapping.md and from general information-security practice of the kind ADHICS requires. They are paraphrases of what an ADHICS control in that area asks for, **not** the wording of a control. Every row is therefore "to verify" as to wording, even when its status below is "met".
3. **V2 may differ**: it may add, merge or relax controls (the cloud rule above all). A reviewer with the V2 PDF should walk the table once and fix ids, wording and tier.
4. **Which tier and which category.** Whether this service is a regulated entity, a "third party" or a "Healthcare Technology and Services Provider" in V2, and which tier applies, is to verify (regulatory-mapping.md section 3).

The V2 domain list used to group the rows below is the vendor summary (11 areas: human resource security, asset management, physical and environmental security, access control, communications and operations, data privacy and protection, cloud security, third-party security, system development, incident management, information systems continuity). I added **governance and risk**, which another summary lists as a V2 area. The grouping is a convenience, not the standard's numbering.

## 2. Status values

| Status | Meaning |
|---|---|
| met | The technical part of the control that software can meet is built, tested, and cited. The operator's procedures may still be needed, and the row says so. |
| partly | Part is built, or the control is built against a stand-in, or a deployment item remains that the row names. |
| not met | Not built and not designed, or no evidence exists. Honest gaps. |
| not applicable | The control concerns something this system does not have (premises, staff of an operator that does not exist yet). Re-assess for a pilot. |
| to verify | Applicability or content cannot be settled from the sources read. |

"Evidence" is a file, a test id from [TEST-CASES.md](../../project/TEST-CASES.md), a make target, or a stated gap. T-E21 requires that every row points to a code path, a test, or a stated gap.

## 3. The table

Row ids (`GV-1`, `AC-3`...) are this document's own, for cross-reference. They are not ADHICS ids.

### 3.1 Governance and risk

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| GV-1 | Risk assessment (id to verify) | Assess information security and privacy risks periodically and keep a risk register | Hazard log with likelihood, impact, controls and tests; red-team threat model; this document set; DPIA draft | partly | [hazard-log.md](hazard-log.md), [red-team.md](red-team.md), [dpia.md](dpia.md), T-E7 (`make docs-check`), T-D21. Not a formal ISMS risk register; ratings are the author's judgement |
| GV-2 | Roles and responsibilities (id to verify) | Named security and privacy roles (security officer, data protection lead) | Roles are named by title in the hazard log (owner column); nobody is appointed | not met | Hazard log "Owner" column; sign-off table in [dpia.md](dpia.md) section 12 is pending |
| GV-3 | Policies and procedures (id to verify) | Documented security policies approved by management | Design records only (ADRs, governance pages); no approved policy set | not met | [DECISIONS.md](../../project/DECISIONS.md); gap |
| GV-4 | Compliance monitoring and audit (id to verify) | Periodic self-assessment, internal audit, regulator survey | A design mapping (this page). The Malaffi security assessment is described as an excerpt of the ADHICS survey and has not been done | not met | regulatory-mapping.md section 5; gap |
| GV-5 | Applicability and tier (id to verify) | Know whether the entity or service is in scope and at which tier | Open | to verify | regulatory-mapping.md section 3 "To verify" |

### 3.2 Human resource security

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| HR-1 | Screening, terms and confidentiality for staff with access (id to verify) | Vet staff and bind them to confidentiality | The prototype has no operator staff with real access. A pilot operator and the provider's staff are subject to their own contracts | not applicable | Re-assess at pilot; [pilot-hardening.md](pilot-hardening.md) |
| HR-2 | Security awareness and training (id to verify) | Train staff on security and privacy | Not designed. AI literacy training is also asked by the DoH Responsible AI Standard (section 3.4) | not met | Gap; regulatory-mapping.md section 4 |
| HR-3 | Leavers and role changes (id to verify) | Remove access promptly when staff leave or change role | Access tokens expire in 5 minutes in the demo realm, so a removed user loses access in that time once the identity provider disables them; no procedure says who disables accounts | partly | [identity.md](identity.md) "Revocation" and "Owner scoping and provisioning"; T-A76; HZ-38 |

### 3.3 Asset management

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| AM-1 | Inventory of information assets (id to verify) | Keep an inventory of information and systems, with owners | The 18 places personal data can live are listed with erasure behaviour; services and images are listed in compose. No asset register with owners | partly | [erasure.md](erasure.md) section 1; `docker-compose.yml`; [dpia.md](dpia.md) section 3 |
| AM-2 | Classification and handling of health information (id to verify) | Classify information and apply handling rules | No classification scheme. In practice: identifiers and clinical resources in HAPI, opaque ids only in the event log, strict payload schemas, no free text in events | partly | `api/app/payloads.py`, ADR-019, T-A36, T-F10 |
| AM-3 | Software and component inventory (id to verify) | Know what software is deployed and its versions | Images pinned by tag (HAPI v8.12.0-2, Keycloak 26.0.7, proxy by tag and digest); dependencies not fully pinned; no software bill of materials | partly | ADR-006, F12, `docker-compose.yml`; [regulatory-mapping.md](regulatory-mapping.md) "dependency scanning is future" |
| AM-4 | Secure disposal and sanitisation (id to verify) | Dispose of information and media securely | Erasure deletes and expunges from HAPI (verified 404), tombstones text, replaces the reference. Backups, replicas and logs are not covered | partly | `api/app/erasure.py`, T-A79, [erasure.md](erasure.md) section 6 |

### 3.4 Physical and environmental security

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| PE-1 | Physical security of facilities and equipment (id to verify) | Control physical access to rooms and equipment holding health information | The prototype runs on a developer laptop with synthetic data. A pilot inherits the hosting facility's controls (provider data centre, UAE cloud) | not applicable | Re-assess at pilot; hosting order in ADR-026 |

### 3.5 Access control

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| AC-1 | Access control policy and least privilege (id to verify) | Grant the minimum access needed for the role | Six roles plus service identities; default-deny route table; event-by-event policy; actor table per automated actor; matrix generated from the running API and compared with the policy | met | [rbac-matrix.md](rbac-matrix.md), `api/app/policy.py`, T-F9, T-A51, T-A60 |
| AC-2 | User registration, provisioning and deprovisioning (id to verify) | Formal process to create, change and remove accounts | Accounts come from the identity provider. No procedure for who creates, disables or reviews them | not met | [identity.md](identity.md) "Owner scoping and provisioning"; HZ-38 |
| AC-3 | User authentication and strong authentication (id to verify) | Unique ids; strong authentication, multi-factor for sensitive access | OIDC authorization code flow with PKCE, JWT verification against the provider's keys, built and tested against a local Keycloak. UAE PASS is not integrated; passwords only, no MFA policy; dev login exists only with `APP_ENV=dev` | partly | `api/app/oidc.py`, `api/app/auth.py`, T-A76, `make test-oidc`, [identity.md](identity.md) |
| AC-4 | Session and token management (id to verify) | Expire sessions; revoke on need | Access tokens last 5 minutes (demo realm), dev tokens 8 hours; `exp` and `nbf` checked on every request. No introspection, no revocation, no back-channel logout; tokens in `sessionStorage` | partly | `api/app/oidc.py`, T-A76, HZ-33, HZ-38, [identity.md](identity.md) |
| AC-5 | Application and information access restriction (id to verify) | Restrict access to information by need; separate customers and patients | Clinicians act only on cases they own; a patient sees only their own case (another patient's case answers 404); replies readable by coordinator and supervisor only | met | rbac-matrix.md (A/S cells), T-F9, T-D19, RT-Z cases (T-D21), `api/tests/test_owner_scope_under_lock.py` |
| AC-6 | Privileged access (id to verify) | Control and monitor administrator access | The API runs as a least-privilege database role that cannot alter tables, disable triggers or truncate. Supervisors are application-level privileged users. A database superuser can still drop the append-only triggers (L30). No privileged-access management | partly | ADR-029, `api/migrations/009_app_role.sql`, `api/tests/test_app_role_pg.py`, L30 |
| AC-7 | Access rights review (id to verify) | Review access rights at intervals | An operating procedure for the provider and operator; not built | not met | [regulatory-mapping.md](regulatory-mapping.md) section 3 table; gap |
| AC-8 | Service and system accounts (id to verify) | Separate, controlled non-human credentials | Each service has its own static token and a fixed allow-list (scheduler, watchdog, automation, agency, HL7). The API refuses to start outside dev with a default token. No rotation mechanism and no vault | partly | `api/app/auth.py` `secure_config_problems`, `api/tests/test_prod_config.py`, ADR-013, ADR-029, rbac-matrix.md |
| AC-9 | Remote access and support access (id to verify) | Control remote administration; in-country support for health information | Not designed. The Responsible AI Standard requires remote support access from within the UAE | not met | Gap; regulatory-mapping.md section 4 |

### 3.6 Communications and operations

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| CO-1 | Documented operating procedures (id to verify) | Procedures for operation, startup, failure | Clock modes, leader election, failure modes table, run commands | partly | [operations.md](operations.md), [pilot-hardening.md](pilot-hardening.md). Not an operator runbook for a hospital environment |
| CO-2 | Change management (id to verify) | Controlled, recorded, tested changes | Git history, ADRs, CI checks (`make check`), migrations applied by a one-shot job, decision log; no change-approval board | partly | `Makefile` `check`, `api/migrations/`, [DECISIONS.md](../../project/DECISIONS.md), T-E7 |
| CO-3 | Capacity and availability (id to verify) | Monitor capacity; plan resilience | 100,000-case sweep measured; two API replicas and two schedulers with fenced leader election; Postgres is a single point of failure (L20) | partly | [operations.md](operations.md), T-F8, T-A78, L20, L34 |
| CO-4 | Malware protection (id to verify) | Protect hosts against malicious code | Concerns the hosts, not the application. The Synthea jar is treated as untrusted and run only in a container with a pinned hash | not applicable | `tools/synthea/SHA256SUMS`, [data-provenance.md](../data-provenance.md); operator's host control at pilot |
| CO-5 | Backup (id to verify) | Back up information and test restores | No backup design. The interaction with erasure is a known gap: a restore resurrects erased data | not met | [erasure.md](erasure.md) section 6 item 2; HZ-34; gap |
| CO-6 | Monitoring and logging (id to verify) | Log security-relevant events with user and time; protect and review logs | Append-only event log of every state change with opaque actor and time; provenance; heartbeats and watchdog alerts; logs hold opaque ids only. No central security log, no log shipping, no review procedure | partly | ADR-004, A11 (`watchdog/main.py`), T-A8, T-A33, T-A30, T-F10, T-F11; centralised logging is a deployment item |
| CO-7 | Clock synchronisation (id to verify) | Keep system clocks synchronised to a trusted source | `CLOCK_MODE=wall` reads real UTC time and is required outside dev; the host time source is not verified; events dated in the future or backdated are refused | partly | `api/app/clock.py`, T-A77, T-F4, [pilot-hardening.md](pilot-hardening.md) "Wall-clock mode" |
| CO-8 | Vulnerability and patch management (id to verify) | Identify and fix vulnerabilities | Pinned images change by deliberate edit; secrets scan, compose lint and red-team suite in the check; no software-composition scan, no patch cadence, no penetration test | partly | `make test-security`, T-F12, `tools/check/compose_lint.py`, [red-team.md](red-team.md) ("not a penetration test") |
| CO-9 | Network security and segmentation (id to verify) | Segment and filter networks | Every published port is bound to 127.0.0.1; services talk over the compose network; scheduler and watchdog have no database connection. No segmentation design for a deployment; MLLP is clear text | partly | `docker-compose.yml`, `tools/check/compose_lint.py`, T-F12, [hl7-adapter.md](hl7-adapter.md) "Not built" |
| CO-10 | Encryption in transit (id to verify) | Protect information in transit | Outside dev the API requires an https identity-provider issuer; model endpoints must be https (plain http only for localhost). No TLS termination, mutual TLS or VPN is part of the stack; local traffic is plain HTTP | partly | `api/app/oidc.py`, `api/app/ai/adapters.py`, `api/tests/test_ai_adapters.py`, `api/tests/test_prod_config.py`; gap for the deployment |
| CO-11 | Cryptographic controls and encryption at rest (id to verify) | Encrypt stored health information; manage keys | Not designed. Database and disk encryption would be provided by the hosting environment; key management is not addressed. Tokens are signed (RS or ES algorithms only) | not met | Gap. Dev tokens HMAC-signed (ADR-012, development only) |
| CO-12 | Information exchange with external parties (id to verify) | Agreements and controls for exchanging health information | One inbound HL7 v2 adapter (sender authority, idempotency, ACK/NAK, no PHI in logs). No outbound path to Malaffi or any EMR. Unconfirmed by the operator | partly | [hl7-adapter.md](hl7-adapter.md), A28, ADR-039, T-A75, RT-H cases (T-D21), [malaffi-adapter-plan.md](malaffi-adapter-plan.md) |
| CO-13 | Electronic messaging to patients (id to verify) | Protect electronic messages | Messages carry no result, diagnosis or identifier (validator); approval or signed templates only; consent checked at send time. The channel is a mock | partly | `api/app/ai/validate.py`, ai-boundary.md section 4a, T-D1, T-D8, T-A25, L26 |
| CO-14 | Separation of environments and test data (id to verify) | Separate development from live; protect test data | Synthetic data only; dev switches (dev login, simulation clock, demo approval) are refused outside `APP_ENV=dev` | met | `api/app/auth.py`, `api/tests/test_prod_config.py`, T-A77, [data-provenance.md](../data-provenance.md), ADR-029 |

### 3.7 Data privacy and protection (health information)

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| DP-1 | Minimum necessary information (id to verify) | Collect and disclose only what the purpose needs | Strict per-event payload schemas reject unlisted keys and free text; case records hold identifiers, state and times; model prompts hold no name, contact or history; agents never see patient text | met | `api/app/payloads.py`, ADR-019, T-A36, T-A66, T-D8 |
| DP-2 | Patient consent and preferences (id to verify) | Obtain and honour consent for use and sharing | Consent ledger per channel and for data sharing; withdrawal freezes outreach; send-time check. How consent is captured at screening is the provider's process | partly | A6, ADR-017, T-A23, T-A24, T-A25, T-F7 |
| DP-3 | Patient access to own information (id to verify) | Let patients obtain their information | Per-case timeline of the event log (A9). No complete export, no patient request route | partly | T-A28; [dpia.md](dpia.md) section 8 |
| DP-4 | Retention schedule (id to verify) | Define how long information is kept and what is done at the end | No schedule. Federal Law No. 2 of 2019 is reported (secondary) to require 25 years for health data; the conflict with erasure is unsettled | not met | [erasure.md](erasure.md) section 6 item 1; HZ-34; **new HZ-41**; [dpia.md](dpia.md) section 7 |
| DP-5 | Erasure and correction (id to verify) | Delete or correct on a valid request | Built for the patient: two supervisors, HAPI delete and expunge verified, text tombstoned, reference replaced, audit trail kept. Not for staff data, backups, logs or open cases | partly | `api/app/erasure.py`, `api/migrations/016_erasure.sql`, T-A79, HZ-34 |
| DP-6 | No health information in logs or identifiers (id to verify) | Keep health information out of logs and URLs | Opaque actors; reference in a request body, never a URL; marker scans of service logs, access logs and stored events; the HL7 adapter logs a hash of sender and control id only | met | T-A36, T-F10, T-F11, `tools/check/phi_scan.py`, `api/tests/test_hl7_logs.py` |
| DP-7 | Secondary use, de-identification and sharing with third parties (id to verify) | No sharing of identified or de-identified health information without authorisation | No outbound sharing is built. Models are not trained on production data (risk score and reply model: synthetic only). Any future model route or analytics export would need authorisation | partly | [risk-score-card.md](risk-score-card.md), [reply-triage-card.md](reply-triage-card.md), regulatory-mapping.md section 4; HZ-39 |
| DP-8 | Data localisation (id to verify) | Keep health information inside the UAE | Target only. Nothing in the code sends data out unless a person sets an HTTP model provider and turns the network switch on. No deployment exists to inspect | to verify | ADR-026; regulatory-mapping.md section 2; HZ-39; T-D4, T-D6, T-D7 |
| DP-9 | Privacy notice and transparency (id to verify) | Tell patients how information is used | Messages state the result is ready and the care team will talk to the patient; Provenance records prompts and approvals. No privacy notice | partly | ai-boundary.md section 4a, T-D5; gap |

### 3.8 Cloud security

This is the control the project has to be most honest about.

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| CL-1 | **Cloud use for health information (v0.9 CM 4.2, Basic)** | A healthcare entity shall not use cloud services or infrastructure to store, process or share information that contains health information, unless authorised by the Abu Dhabi health sector regulator (DoH) | **Not met for any real-data deployment.** No DoH authorisation exists and none has been requested. ADR-026 sets the hosting order: the provider's own approved environment first, then a DoH-authorised UAE cloud (Azure UAE North or Core42 only after written authorisation), nothing else. The prototype holds synthetic data and runs on a laptop, so the control is not triggered by it today. The Azure and Core42 position rests on vendor and region-list statements, not on DoH acceptance | not met | ADR-026, L22, regulatory-mapping.md sections 3 and 6, [pilot-hardening.md](pilot-hardening.md) "DoH cloud authorisation" |
| CL-2 | CM 4.2 (cloud rule in V2, id to verify) | Whether V2 keeps, relaxes or rewords the cloud rule; vendor summaries say V2 has a dedicated cloud security domain and asks for encryption of cloud-hosted data and for cloud services in the asset inventory | Unread. A pilot must not rely on any reading of V2 until the text is checked | to verify | regulatory-mapping.md section 3 "To verify" item 2; this page section 1 |
| CL-3 | CM 4.2 (no transmission outside the UAE) | Ensure health information is not transmitted outside the UAE | No component sends data out by default; the model route is off (`LLM_NETWORK_ENABLED=false`); the reply classifier runs on the same host. Not demonstrated on a deployment | partly | `api/app/ai/adapters.py`, T-D4, T-D7, [dpia.md](dpia.md) section 6, HZ-39 |
| CL-4 | CM 4.2 (identify and disconnect cloud integrations) | Know every integration with a cloud service and disconnect those not allowed | The only possible cloud integrations are the optional model adapters (azure_openai, jais), off by default and unit-tested with no live endpoint. There is no Anthropic adapter. CI uses a hosted runner on synthetic data only | partly | `api/app/ai/adapters.py`, T-D6, L25, ai-boundary.md section 3; [dpia.md](dpia.md) section 6.3 |
| CL-5 | CM 4.2 (no sharing with third parties without authorisation) | Do not share identified or de-identified health information with third parties unless authorised | No outbound sharing path is built. A hosted model would be a third party; the template fallback keeps the product working without one | partly | ADR-007, ADR-026, HZ-39 |
| CL-6 | Backup and disaster recovery location (id to verify) | Keep backups and DR sites inside the UAE | Not designed; the requirement is recorded. UAE Central (restricted) as a DR site is unverified | not met | regulatory-mapping.md section 6 "To verify" |

### 3.9 Third-party security

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| TP-1 | Third-party agreements and security requirements (id to verify) | Contracts with security and privacy terms; assessment before use | No agreement exists with any provider, host, Malaffi or model vendor. Required terms are listed in the DPIA | not met | [dpia.md](dpia.md) sections 2.2 and 6.3; gap |
| TP-2 | Service delivery monitoring (id to verify) | Monitor and review third-party performance and compliance | Not applicable until a third party exists | not applicable | Re-assess at pilot |
| TP-3 | Supply chain and outsourced development (id to verify) | Control components and external development | Synthea jar pinned by sha256 and run in a container; images pinned; Python and Node dependencies not fully pinned; no software-composition scan; models used are open weights (Qwen2.5 base, Apache-2.0) or optional hosted APIs | partly | `tools/synthea/SHA256SUMS`, ADR-006, F12, [reply-triage-card.md](reply-triage-card.md) section 2; gap |
| TP-4 | Status of this service under ADHICS (id to verify) | Healthcare technology and services providers have their own obligations (V2 vendor summaries) | Unknown whether this service is a regulated entity, a third party of the provider, or a service-provider category | to verify | regulatory-mapping.md section 3 "To verify" item 1 |

### 3.10 System development

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| SD-1 | Security requirements and secure development (id to verify) | Build security in; document requirements | Threat model and abuse cases written before the red-team tests; ADRs record security decisions; default-deny; fail-closed start | partly | [red-team.md](red-team.md), ADR-012, ADR-029, ADR-040. No formal secure-development policy |
| SD-2 | Security testing (id to verify) | Test security before release | 737 red-team cases (727 pass, 10 recorded known limitations, 0 fail), RBAC matrix, PHI scans, secrets scan, compose lint, model checking of the closure engine. No independent penetration test | partly | T-D21, T-F9, T-F10, T-F11, T-F12, T-A74, `make test-redteam`, `make test-security` |
| SD-3 | Input validation and data integrity (id to verify) | Validate input; protect integrity | Strict payload schemas; strict HL7 parsing with size limits; output validation of model text; idempotent event ids; derived state checked against replay on every append; append-only triggers | met | `api/app/payloads.py`, T-A36, T-A75, T-D8, T-F3, T-A8, T-A9, ADR-002, ADR-004 |
| SD-4 | Secrets management (id to verify) | Keep secrets out of code and protect them | Secrets only in environment variables; `.env` ignored; scan of tracked files and history; demo passwords generated per run into a git-ignored file. No vault or rotation | partly | T-F12, `tools/check/secrets_scan.py`, [identity.md](identity.md) |
| SD-5 | Tamper resistance of the audit trail (id to verify) | Protect audit records against change | Triggers reject update, delete and truncate; the only exception is the erasure function, which touches one case and is tested. A superuser can drop the triggers | partly | ADR-004, ADR-025, T-A8, T-A79, L30 |
| SD-6 | Validation and control of AI components (DoH Responsible AI Standard, not an ADHICS row) | Human oversight, traceability, in-country compute, no training on production data | See [ai-boundary.md](ai-boundary.md) and regulatory-mapping.md section 4. Listed here so the AI controls are not lost between two documents | partly | T-D1, T-D2, T-D10, T-D17 to T-D20, T-D5 |

### 3.11 Incident management

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| IM-1 | Incident response plan and roles (id to verify) | Plan, roles and procedures for incidents | Not designed for production. Operational failures are tabulated; chaos tests cover scheduler loss and duplicate events | not met | [operations.md](operations.md) "Failure modes"; T-F3, T-F4, T-F5; gap for security incidents |
| IM-2 | Reporting incidents to the regulator (id to verify) | Report to DoH within the stated timelines using the prescribed forms | Timelines are unverified: one vendor page reports a 72-hour breach form and a 4-hour containment window for high-risk incidents; another says no universal period. Not designed | not met | Gap; this page section 1 |
| IM-3 | Detection and evidence (id to verify) | Detect incidents; keep evidence | Watchdog alerts (stopped scheduler, idle cases); append-only log and provenance give a trail; no intrusion detection | partly | A11, T-A30, T-A33, HZ-03, HZ-04 |
| IM-4 | Breach notification to patients (PDPL, not an ADHICS row) | Notify affected people where required | Not designed; PDPL duties and timelines unverified | not met | regulatory-mapping.md section 1 "To verify" |

### 3.12 Information systems continuity

| Row | ADHICS area (id) | Requirement summary | How the system meets it | Status | Evidence |
|---|---|---|---|---|---|
| BC-1 | Continuity and disaster recovery plan (id to verify) | Plan and test recovery | No DR plan. Application-level resilience is tested (leader failover in 10.1 s, no event lost; killed sweep loses nothing). Postgres is a single point of failure | partly | [operations.md](operations.md), T-A78, T-F5, L20; gap for DR |
| BC-2 | Graceful degradation of critical functions (id to verify) | Keep the essential function when a component fails | The engine does not depend on any model; template fallback; every failed classification goes to a person; the lease lapse is a performance event, never a data event | met | ADR-007, ai-boundary.md section 5, reply-triage-card.md section 7, T-D3, T-D4, T-A78 |
| BC-3 | Restore testing (id to verify) | Test backups by restoring | None, because there is no backup design (CO-5) | not met | Gap |

## 4. Counts

Counted from the status cells of the rows in section 3 by a script at writing time (recount if a row changes).

| Status | Rows |
|---|---|
| met | 7 |
| partly | 36 |
| not met | 17 |
| not applicable | 4 |
| to verify | 4 |
| **Total** | **68** |

Two of the 68 rows are cross-references that are not ADHICS rows (SD-6, the DoH Responsible AI Standard, and IM-4, PDPL breach notification to patients); they are in the table so those controls are not lost between documents. Without them: 7 met, 35 partly, 16 not met, 4 not applicable, 4 to verify, 66 in all (SD-6 is "partly", IM-4 is "not met").

A "met" row means the technical part is built and tested for the synthetic prototype; it never means a deployment meets the standard. Section 1 says why even the "met" rows are to verify as to wording.

## 5. Reading the result

**Not met (17):** GV-2, GV-3, GV-4, HR-2, AC-2, AC-7, AC-9, CO-5, CO-11, DP-4, CL-1, CL-6, TP-1, IM-1, IM-2, IM-4, BC-3.

**To verify (4):** GV-5, DP-8, CL-2, TP-4.

The largest groups of gaps are: **hosting and cloud** (CL-1 is the blocker for any real data), **operations that need an operator** (backup, DR, incident response, access review, training), **cryptography and key management** (CO-11), **third-party governance** (TP-1) and **retention** (DP-4). The strongest area is application-level access control, minimisation, input validation and graceful degradation.

Walk-through for a reviewer with the V2 PDF: (1) replace "id to verify" with the control id; (2) check the wording of each requirement summary; (3) check the tier of each row; (4) re-rate rows whose V2 text differs; (5) read the V2 cloud domain and update CL-1 to CL-6 first.
