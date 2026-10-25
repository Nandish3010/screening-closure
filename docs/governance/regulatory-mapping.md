# Regulatory mapping

Status: draft for human review. This is a design-time mapping, not legal advice and not a compliance claim. The prototype holds synthetic data only (see [data-provenance](../data-provenance.md)), so none of these regimes applies to the prototype itself. The mapping describes how the design would meet them in a deployment, and lists what has not been verified.

Reading rules used throughout:

- **Verified** means the text was read in a primary source (the regulator's own document or portal) on the access date shown.
- **Secondary** means a law firm, government trade or vendor summary was read, not the instrument itself.
- **To verify** means not established by the sources read. Every "to verify" item needs a legal or regulator check before it is cited as fact.
- Article numbers are given only where a source read gave them. Several PDPL provisions are described without article numbers because the sources read did not give them.

Companion documents (E18): the data protection impact assessment, structured on PDPL expectations and GDPR Article 35 practice, is [dpia.md](dpia.md); the control-by-control ADHICS table, with the cloud rule CM 4.2 stated plainly, is [adhics-controls.md](adhics-controls.md). Neither changes the readings below; they use them and add what they found (the Federal Law No. 2 of 2019 retention figure, secondary, in dpia.md section 7; the failure to retrieve ADHICS V2 on 2026-10-25, in adhics-controls.md section 1).

All URLs in this document were accessed on **2026-10-18** unless a different date is shown. The full list is in the section "Sources and access dates" at the end.

## Summary table

| # | Instrument or body | What it is, in one line | Status of our reading | Biggest open question |
|---|---|---|---|---|
| 1 | UAE Federal Decree-Law No. 45 of 2021 (PDPL) | Federal personal data protection law, in force 2 January 2022 | Secondary plus one official portal page | Whether it applies to health data at all, given the exclusion for data governed by other legislation |
| 2 | UAE Federal Law No. 2 of 2019 (ICT in health fields) | Health-sector data law with a localisation rule | Secondary only | Exact text of the localisation article and of the exceptions in Ministerial Decision 51 of 2021 |
| 3 | ADHICS (Department of Health, Abu Dhabi) | Mandatory information and cyber security standard for DoH-regulated entities | Primary for v0.9 (2019); current version (V2, 2024) not retrieved | Current cloud rule: v0.9 control CM 4.2 prohibits cloud use for health information unless DoH authorises |
| 4 | DoH Abu Dhabi AI policy and standard | 2018 policy plus a 2025 Responsible AI Standard | Primary for both | Whether anything newer applies; how our two AI uses are risk-classified |
| 5 | Malaffi | Abu Dhabi health information exchange | Primary (public site) | Whether any FHIR interface exists; participation route for a non-provider vendor |
| 6 | In-country hosting (Azure UAE North, Core42) | Hosting options inside the UAE | Primary for Azure region list; vendor claims for Core42 | Whether either option is acceptable to DoH for health information, given item 3 |

## 1. UAE Federal Decree-Law No. 45 of 2021 on the Protection of Personal Data (PDPL)

### What it requires (as read)

| Topic | What the sources say | Source type |
|---|---|---|
| Status and scope | In force from 2 January 2022. Covers processing of personal data by electronic means, inside or outside the UAE, by UAE-based controllers and processors and by those outside the UAE who process data of people in the UAE. | Official portal page (u.ae), secondary |
| Exclusions | Government data and public entities, security and judicial authorities, bank and credit data governed by other law, and free zones with their own regimes (DIFC, ADGM are the usual examples). Health personal data is excluded where governed by other legislation. | Secondary (Simmons & Simmons, December 2021). The u.ae page mentions the separate health ICT law but does not state the exclusion. |
| Lawful basis | Consent is the primary basis and must be specific, clear and unambiguous, and the controller must be able to demonstrate it. Other bases exist (for example performance of a contract, legal obligation, public interest). The sources disagree on whether legitimate interests is a basis; one says it is not. | Secondary, conflicting |
| Data subject rights | Access to and a copy of data, rectification, erasure, restriction or suspension of processing, objection. Controllers must provide clear means to exercise them. | Secondary; u.ae page confirms correction and restriction |
| Cross-border transfers | Allowed to jurisdictions with adequate protection; otherwise by contract ensuring equivalent protection, or with explicit consent, or where needed to perform a contract in the data subject's interest. | Secondary |
| Data Protection Officer | Required in defined high-risk cases (new technology or large volumes, systematic evaluation of sensitive data, large-scale sensitive data). | Secondary |
| Regulator | UAE Data Office. | u.ae page |
| Executive Regulations and penalties | Sources conflict on whether Executive Regulations have been issued and on penalty amounts. | Secondary, conflicting |

### How our design meets it

| Obligation | Design response | State today |
|---|---|---|
| Lawful basis and consent | A consent ledger records consent per contact channel and for data sharing as an append-only history; withdrawal freezes outreach and flags the case for a human, and the send-time check sits in the notification mock (A6, A7, ADR-017, ADR-018, T-A23, T-A24, T-A25, T-F7). For a deployment the lawful basis for the screening programme itself is a provider decision (to verify), and our service would act as processor for the provider. | Built for the ledger, see A6, ADR-017, T-A23, T-A24; the lawful basis is future (to verify) |
| Data minimisation | Case records carry identifiers, result reference, state and timestamps; they do not copy the clinical record. Only the fields needed to write a message are used in drafting. Negatives are not stored by the simulator. The event log holds opaque ids and strict per-event payload schemas that reject unlisted keys and free text (A14). | Built, see A14, ADR-019, T-A36 (stored events), T-F10 and T-F11 (logs, URLs) |
| Purpose limitation | The only purpose is tracking and closing the follow-up of an abnormal screening result. The risk score is used only to order the worklist. | By design, stated in ADR-007 |
| Data subject rights: access | A per-case timeline from the event log lists everything the log holds about a case (`GET /cases/{id}/timeline`, A9, T-A28). It does not cover the HAPI resources or the notification and draft texts, so a complete subject-access export is not built. | Built in part, see A9, T-A28; the complete export is future |
| Data subject rights: rectification | Corrections are new events that supersede earlier ones; the original stays in the log. | By design |
| Data subject rights: erasure | Tension: the event log is append-only by design (ADR-004), so deletion is not possible in place. Design accepted (ADR-025) and built (A32): personal data is kept out of the log, and an erasure, requested by one supervisor and approved by another with a reason from a fixed list, deletes and expunges the Patient and its resources in HAPI, overwrites message, reply and prompt text with a tombstone, replaces the patient reference in the log with a random token (the map is then deleted) and records `erasure_completed` with only an erasure id and categories (`docs/governance/erasure.md`). Also built earlier: opaque `role:user_id` actors, strict payload schemas, an erasable `users` table (A14, ADR-019). Not covered: backups and replicas, staff data. The interaction with mandatory health record retention (item 2) is NOT settled and the code does not check it: **to verify** before erasure is used on real data. Whether the remaining log counts as anonymised is a legal question, to verify. | Built (A32, T-A79); legal basis, retention and backups open |
| Security | Role-based access with default-deny policy tables (A8, A14, F7, [rbac-matrix.md](rbac-matrix.md), ADR-012, ADR-019); no patient data in URLs or logs (F8, T-F10, T-F11); secrets scan and compose lint in `make test-security`; append-only log with database triggers (ADR-004); a least-privilege database role and a production fail-closed start (SEC-1, ADR-029, migration 009_app_role.sql); every published port bound to 127.0.0.1. People sign in through OIDC against a local Keycloak that stands in for UAE PASS, and tokens expire (A29, ADR-040, [identity.md](identity.md)); UAE PASS itself is not integrated and its claims and assurance level are unverified. | Built, see A8, A14, F7, A29, ADR-012, ADR-029, ADR-040; UAE PASS integration, MFA policy and revocation are to verify |
| Cross-border transfer | The deployment target is in-country hosting (item 6). No component sends data outside the UAE; the model provider for drafting would have to be in-country too, or the template fallback is used. | Deployment requirement |
| Prototype data | Synthetic data only. No real patient data enters the repository, tests or the demo. | Done |

### To verify

The impact assessment for a deployment is [dpia.md](dpia.md).

1. Whether PDPL applies to the pilot's data at all, or whether the health-sector rules in items 2 to 4 are the governing regime for health data (secondary sources say health data governed by other legislation is excluded; the instrument was not read).
2. The status of the Executive Regulations and the penalty provisions.
3. Whether the consent and rights model needs to be built to PDPL standard for non-health personal data such as staff accounts and contact details held outside the health record.
4. Article numbers for each obligation, from the official text on the UAE legislation portal.

## 2. UAE Federal Law No. 2 of 2019 on the Use of Information and Communication Technology in Health Fields

### What it requires (as read, secondary only)

- **Localisation.** Health data relating to health services provided in the UAE may not be stored, processed, generated or transferred outside the UAE, unless an exception applies. One summary puts the rule in Article 13 (with Article 12 on storage inside the UAE); this was not confirmed against the instrument. (Trowers & Hamlins; Baker McKenzie search summary; US International Trade Administration, 22 January 2024.)
- **Exceptions.** Ministerial Decision (Resolution) No. 51 of 2021 introduced exceptions. A US trade summary lists ten types that health authorities can allow case by case: pharmacovigilance reporting, treatment of overseas patients, insurance claims, scientific research and clinical trials, wearable monitoring devices, medical diagnostic testing, cooperation with UAE government institutions, telemedicine, personal use, and other purposes approved with the federal Ministry of Health and Prevention. Another firm summary names treatment of overseas patients and medical samples among them. The sources differ in wording and in the year of the decision (2020 or 2021 appears), so the list is to verify against the decision itself.
- **Confidentiality and purpose.** Health data is confidential and may be circulated only with proper authorisation, generally the patient's consent, and used only for the purpose for which it was obtained. Sharing with insurers for benefit approval and de-identified research use are permitted in defined cases.
- **Integrity.** Data must be protected against unauthorised alteration, deletion and destruction.
- **Retention.** The law is commonly described as requiring health records to be kept for a minimum period; the period was not stated in the sources read. To verify.

### How our design meets it

| Obligation | Design response |
|---|---|
| Localisation | Deployment target is hosting wholly inside the UAE, including backups and disaster recovery, with remote support access also from within the UAE (this also matches the Responsible AI Standard, item 4). No analytics, logging, monitoring or model-inference service may sit outside the UAE; this includes model inference logs and any vector store, which would count as health data if they hold identifiable content. The prototype runs locally on synthetic data. |
| Confidentiality and purpose | Role-based access so each role sees only its scope; consent ledger for sharing; purpose limited to follow-up tracking. |
| Integrity | Append-only event log with triggers that reject update, delete and truncate (ADR-004); derived state checked against replay on every append (ADR-002). A database superuser can still remove triggers; production adds access controls and log shipping (recorded as a limitation). |
| Retention | The event log is retained as the audit trail. The retention period and the policy for what is deleted at the end are not set (to verify). Erasure is built (A32, [erasure.md](erasure.md)) and the code does not check the retention period; whether the retention duty forbids or limits erasure of the clinical record is to verify with the data protection lead and the DoH (item 1 and the list below). |

### To verify

1. Exact text and article numbers of the localisation rule and of the exceptions, from the instrument and Ministerial Decision 51 of 2021.
2. Whether a vendor providing tracking software to a provider is itself in scope, and whether a processing arrangement within the UAE is sufficient.
3. The minimum retention period.

## 3. Abu Dhabi Healthcare Information and Cyber Security Standard (ADHICS)

### Scope (as read)

The Department of Health (DoH) Abu Dhabi issued ADHICS and announced it on 2 March 2019, with compliance expected by 29 February 2020 (DoH news item). The version read in full is **v0.9, published 3 February 2019** (document reference DOH/SD/ADHICS/0.9, 116 pages). It covers all DoH-regulated healthcare entities and services in the Emirate of Abu Dhabi, applicable to healthcare and medical facilities, professionals and support staff with access to patient health, diagnostic or personal information, diagnostic labs, pharmacies and insurance providers. It covers information in physical and digital form, devices, applications, infrastructure and the people supporting care delivery, and it applies to the entity's use of partner and third-party systems within the Abu Dhabi ecosystem, naming the Shafafiya portal, the health information exchange platform and DoH e-services. Which controls apply depends on the entity's maturity, complexity and risk.

A secondary source reports a later **V2 (reference DOH/SD/ICSO/ADHICS/V2/2024, published May 2024, effective August 2024)** that replaced earlier standards. The V2 document was not retrieved. Anything below that depends on version is therefore based on v0.9 and is to verify against V2.

Control areas in v0.9 that matter to us (all from the table of contents and text read): access control (user access management, application and information access control, access reviews), operations management (backup, monitoring and logging), communications (information exchange, information sharing platforms), health information protection and privacy, third-party security (service delivery and monitoring), systems acquisition and development (security requirements, cryptographic controls, outsourced development, supply chain), incident management, and continuity.

**Cloud rule (important).** Control CM 4.2 in v0.9, categorised "Basic", states that a healthcare entity shall not use cloud services or infrastructure to store, process or share information that contains health information; it shall ensure health information is not transmitted outside the UAE, identify and disconnect integrations with cloud services, and not share identified or de-identified health information with third parties unless authorised by the Abu Dhabi health sector regulator. A US trade summary (22 January 2024) adds that DoH can grant case-by-case exceptions for cloud-based data centre services, and DoH's FAQ says cloud controls are in CM 4. Whether V2 keeps, relaxes or reworded this rule is to verify. This directly affects the hosting target in item 6.

### How our design meets it

| Control area | Design response | State today |
|---|---|---|
| Access control | Roles (lab, primary care owner, specialist owner, coordinator, supervisor, patient); event actor derived from identity; per-route policy tested for every role (A8, F7); sign-in through OIDC with expiring tokens, tested against a Keycloak stand-in (A29). Periodic access review is an operating procedure for the provider and the operator; not built. | Built, see A8, A14, F7, [rbac-matrix.md](rbac-matrix.md), ADR-012, ADR-019; access review is a deployment procedure |
| Monitoring and logging | Append-only event log of every state change with actor and time; a watchdog flags idle cases and a stopped scheduler (A11). Centralised security logging for infrastructure is a deployment item. | Built, see A11, ADR-023; centralised logging is a deployment item |
| Information exchange | One outbound path for messages (notification mock in the prototype); an adapter boundary for exchange with Malaffi (the inbound HL7 v2 adapter is built, see [hl7-adapter.md](hl7-adapter.md), and the adapter plan). | Outbound path built, see A7, ADR-018 (a mock that writes to a log); the HL7 v2 adapter is built (A28, ADR-039, [hl7-adapter.md](hl7-adapter.md)) and unconfirmed by Malaffi: no connection to Malaffi and no agreement with its operator exist; an outbound path to Malaffi is future |
| Health information protection | Minimisation by strict payload schemas (A14, ADR-019), consent ledger (A6, ADR-017), no patient data in URLs or logs (T-F10, T-F11). Erasure is built (A32, item 1; [erasure.md](erasure.md)); its conflict with the retention duty and backup handling are to verify. | Built, see A6, A14, A32, ADR-017, ADR-019, ADR-025, T-F10, T-F11, T-A79; retention conflict to verify |
| Third-party security | The model provider is optional and off by default; any provider would need to be in-country and inside the provider's third-party assessment. | By design |
| Secure development | Tests, the HAPI image pinned to a version tag (F12, ADR-006), a secrets scan and a compose lint in `make test-security`, a script-checked fresh clone (F12). Full dependency pinning and a software-composition scan are not built. | Built, see F12, ADR-006, `make test-security`; dependency scanning is future |
| Incident management and continuity | Not designed for production in the prototype. Chaos tests cover scheduler loss and duplicate events (F3, F4). The incident process is a deployment item for the operator. | Deployment item |

### To verify

The control-by-control table is [adhics-controls.md](adhics-controls.md).

1. The current ADHICS version, its control list, whether our service is a "third party" or itself a regulated entity, and which tier (Basic, Transitional, Advanced) would apply.
2. The current cloud rule and the DoH exception route (see item 6).
3. Whether DoH requires a certification or assessment of the vendor, and how it relates to the Malaffi security assessment, described by Malaffi as an excerpt of the ADHICS survey.

## 4. Department of Health Abu Dhabi policy and standard on AI in healthcare

### Current documents (as read, primary)

| Document | Reference | Date | Notes |
|---|---|---|---|
| Policy on Use of Artificial Intelligence (AI) in the Healthcare Sector of the Emirate of Abu Dhabi | Policy/AI/0.9, version 0.9 | 30 April 2018 | Applies to DoH-licensed providers, UAE pharmaceutical manufacturers, insurers, licensed researchers, and every end-user that uses Abu Dhabi population or patient data in AI work. |
| Responsible Artificial Intelligence (AI) Standard | DoH/ST/DDGO/RAI/V1/2025, version V1 | Published and effective October 2025; revision date October 2026 | Owner: Data and Digital Governance Office. States it follows the guiding principles of the DoH AI Policy. Applies to DoH and all licensed healthcare entities in Abu Dhabi that develop, procure or deploy AI systems, including third-party systems. |

No source read states that the 2018 policy was repealed. A 2025 standard that refers back to "the DoH AI Policy" suggests the policy remains the parent document, but whether a newer policy text exists is to verify with DoH. The standard's own revision date (October 2026) is close to the date of this document, so a revised version may be published; check before submission.

### What it requires (selected, as read)

2018 policy: AI treated as supportive technology, with a human making and being responsible for final decisions; principles of transparency (verifiable and explainable), user assistance, safety and security, privacy, ethics and accountability; minimum requirements for AI tools including recognised certification, auditable validation statements, graceful degradation, audits and continuous feedback; end-users must have clear governance, state the point of entry of AI and the boundary between AI and the practitioner, and report incidents to DoH.

2025 standard: human-in-the-loop or human-on-the-loop oversight with escalation protocols (3.1.1.2); clinicians retain final decision-making authority, with override and audit trails (3.1.7.2); designated owners and logging of all decisions and changes traceable to a responsible party (3.1.7.1); transparency and end-to-end traceability logs of inputs, outputs and version history (3.1.6); privacy by design with minimum necessary data and encryption, and production data not used to train models (3.1.4.4.2); compute and storage, including disaster recovery sites, within the UAE and remote access for development and support exclusively from within the UAE (3.1.4.5.1); risk assessment and a risk register, with high-risk systems requiring conformity assessment in coordination with the DoH Data and Digital Governance Office (3.3.1.2.2.2); AI literacy and training (section 3.4). Section numbers are from the document read. This is a paraphrase, not a quotation. The clauses above were re-checked against the official PDF on 2026-10-18 (3.1.1.2, 3.1.4.4.2, 3.1.4.5.1, 3.1.6.2.3, 3.1.7.1, 3.1.7.2, 3.3.1); 3.1.4.4.2 states that production data shall not be used to train the model.

### How our design meets it

| Requirement | Design response |
|---|---|
| Human oversight and final authority | Core design: state transitions are decided by deterministic code and humans; a model never moves a case or decides a closure; closure needs evidence and a human sign-off (ADR-001, T-A1). |
| Point of entry of AI and its boundary | Stated in the design: a model is used only to draft patient messages, behind approval; a simple regression orders the worklist (ADR-007, `docs/design-choices.md`). |
| Transparency and traceability | Draft prompt, model, draft, approver and time stored as Provenance (D4, D5); event log for everything else. Patients are told the message is automated assistance reviewed by their care team. |
| Graceful degradation | The engine does not depend on the model; the template fallback is the default (ADR-007). |
| Validation and monitoring | Output validation, abstention rate reported (D3, D11); calibration check and risk score card for the regression (D10). Post-deployment monitoring is a pilot activity (see the pilot plan). |
| Privacy and no training on production data | Minimal prompt fields; no training on production data. The risk score is trained on simulator data only. |
| In-country infrastructure and remote access | Deployment requirement; applies to the model provider and to our own support staff. |
| Risk assessment | The hazard log (E6) and decision matrix (E5) serve as inputs; formal classification under DoH's risk protocol is the provider's step. |

### To verify

1. Whether our two AI uses (drafting; worklist ordering) fall in the Low, Medium or High category under the DoH risk protocol referred to by the standard (the protocol was not read), and so whether conformity assessment is needed.
2. Whether "certification of the AI software from recognised international agencies" (2018 policy 7.1.2.1.1) applies to a drafting tool, and what DoH accepts.
3. Whether any newer DoH AI policy text exists. (The DoH circular 2026/10 and the IFHAS standard V10 cited elsewhere in the project were read first-hand in the official PDFs; see [evidence.md](../evidence.md) section 8.)

## 5. Malaffi, the Abu Dhabi health information exchange

### What it is and who runs it (as read)

- Malaffi describes itself as Abu Dhabi's health information exchange, enabling providers to exchange patient health information, connecting public and private providers in the Emirate (malaffi.ae, 2026-10-18).
- The site footer names **Abu Dhabi Health Data Services – SP LLC** as the operator. Press coverage from the platform's launch describes it as a public-private partnership between DoH and Injazat, with the platform supplied by Orion Health; a news item on the Malaffi site places it within the M42 group. The current ownership is to verify; the operator named in the footer is the entity to approach.
- All licensed DoH healthcare facilities must connect, with listed exemptions. Onboarding steps listed: accession agreement and facility declaration, the Malaffi security assessment (an excerpt of the ADHICS survey), an HL7-capable EMR, and a network connection supplied through the operator's arrangement. Facilities cannot renew licences or submit certain insurance claims until connected.
- The Malaffi policies page lists the DoH Policy on the Abu Dhabi Health Information Exchange, the ADHICS standard and the DoH Standard on Patient Healthcare Data Privacy. The page does not state consent or opt-out rules; the policy documents were not read.

### Interfaces: are FHIR interfaces publicly documented?

On the public pages read, no. The resources page lists, on request only, "Malaffi Real-Time HL7 Message Specifications" (general, pharmacy and reference laboratory), a participant agreement and participation criteria. The onboarding page requires an HL7-capable EMR and does not mention FHIR. DoH Circular 2026/10 (19 January 2026) was read first-hand in the official PDF (check P, [evidence.md](../evidence.md) section 8c): its attached minimum dataset is expressed as HL7 message types (ADT, PPR, ORU, OMP, RDS, RAS, VXU, SIU), and no official document read names FHIR. The working assumption in this project is therefore **HL7 v2 at the Malaffi boundary, FHIR inside our engine, and FHIR toward Malaffi only if the operator confirms it**, to be confirmed with the HIE operator (hie@doh.gov.ae, a contact printed on the DoH pages, see evidence.md Gaps item 4). Vendor pages that claim FHIR support for Malaffi integration were seen and are not treated as evidence.

### How our design meets or uses it

Malaffi is a stated target, not an integration; nothing in the prototype connects to it. The HL7 v2 adapter is built and unconfirmed by Malaffi (its mappings are our reading of the standard; see [hl7-adapter.md](hl7-adapter.md)); the adapter plan is in [malaffi-adapter-plan.md](malaffi-adapter-plan.md). Design relevance: our engine is FHIR R4 internally; the boundary adapter translates; consent and identity assumptions are listed in the plan.

### To verify

1. Interface standard offered to a non-EMR vendor (HL7 v2 only, or also FHIR), and whether a third-party workflow system can participate directly or only through a connected provider's EMR.
2. Participation criteria and agreement terms (available on request).
3. Whether Malaffi consent or opt-out rules affect which results a downstream workflow system may receive.

## 6. In-country hosting: Azure UAE North and Core42

### What is documented (as read)

- Microsoft's region list (page updated 2026-10-13 per its metadata) shows **UAE North** in Dubai with 3 availability zones, and **UAE Central** in Abu Dhabi with no availability zones and restricted access, the two being paired. Restricted regions are available only to support specific customer scenarios, such as disaster recovery within a geography, on request.
- Microsoft's data-residency statements for these regions, and for the services we would use (database, containers, monitoring, backup, any model service), were not retrieved. To verify service by service, including that metadata, backups and support access stay in the UAE.
- **Core42 Sovereign Public Cloud**: a Core42 brochure (April 2026) describes Microsoft Azure combined with UAE-specific sovereign controls and a compliance application, claims in-country data sovereignty and mapping to the UAE National Cloud Security Policy and more than 200 technical controls. These are vendor statements; no region names, certifications or audit evidence appeared on the page read. The split of operational responsibility between Core42 and Microsoft was not stated.

### How our design meets it

- The stack is containerised (Postgres, an API, a scheduler, a watchdog, web apps, HAPI FHIR) and does not depend on a cloud-specific service, so the same compose-derived deployment can run in a provider's own data centre, a UAE cloud region, or a sovereign cloud.
- The hosting options for a pilot, in order of least regulatory risk to most: (a) inside the participating provider's own data centre or private cloud already approved for health information; (b) a UAE-based cloud approved by DoH for health information; (c) Azure UAE North or Core42 Sovereign Public Cloud, subject to DoH acceptance given ADHICS CM 4.2 and Federal Law No. 2 of 2019.
- Whichever option applies, disaster recovery and backups must also be in the UAE, and so must remote administration (Responsible AI Standard 3.1.4.5.1).

### To verify

1. Whether DoH accepts Azure UAE North or Core42 for health information (the case-by-case exception route, ADHICS V2 cloud control).
2. Service availability and residency in UAE North for each service we would use.
3. Whether UAE Central, which is restricted, is usable as the in-country disaster recovery site, and on what terms.

## Cross-cutting design controls

| Control | Where it appears | State |
|---|---|---|
| Data minimisation | Event model carries references, not the clinical record; prompts carry only message fields | Built, see A14, ADR-019, T-A36, T-F10, T-F11 |
| Consent ledger | Append-only consent history per channel and for data sharing; withdrawal freezes outreach (A6) | Built, see A6, A7, ADR-017, T-A23, T-A24 |
| Append-only audit | Events table with triggers (ADR-004); FHIR Provenance per event (A12, ADR-024, T-A33) | Built, see M0-2, ADR-004, A12, ADR-024, T-A33 |
| Role-based access | Roles and per-route policy, matrix test (A8, A14, F7, [rbac-matrix.md](rbac-matrix.md)) | Built, see A8, A14, F7, ADR-012, ADR-019; OIDC login built against a Keycloak stand-in (A29, ADR-040), UAE PASS to verify |
| Synthetic data only in the prototype | Synthea cohort and seeded simulator; declared in data-provenance | Done |
| In-country hosting as the deployment target | Section 6 | Target, not done |
| Human gates | Closure invariant (T-A1); message approval (T-D1) | Closure guard done in the scaffold; approval gate built (D5, ADR-030, outbox migration 011_outbox.sql, T-D1) |

Retention and deletion policy is not yet set; it depends on the health record retention period and on how the erasure that A32 built is to be reconciled with it (item 1), both to verify with the data protection lead and the DoH.

## Sources and access dates

All accessed 2026-10-18.

Primary (regulator or operator):

- DoH Abu Dhabi, Policy on Use of AI in the Healthcare Sector (Policy/AI/0.9, 30 April 2018): https://www.doh.gov.ae/-/media/E9C1470A575146B18015DEBE57E47F8D.ashx
- DoH Abu Dhabi, Responsible AI Standard V1 (October 2025): https://www.doh.gov.ae/-/media/Feature/Resources/Standards/2025/Responsible-AI-Standard-V1.ashx
- DoH Abu Dhabi, ADHICS v0.9 (3 February 2019): https://www.doh.gov.ae/-/media/032019_ADHICS.ashx
- DoH Abu Dhabi, Circular 2026/10 (19 January 2026), read first-hand for evidence.md section 8c: https://www.doh.gov.ae/-/media/84A213F71AA545D69E01A23A0A99C598.ashx
- DoH Abu Dhabi, IFHAS standard V10 (July 2025), read first-hand for evidence.md section 8: https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx
- DoH Abu Dhabi, news item on the launch of ADHICS (2 March 2019): https://doh.gov.ae/en/news/department-of-health-launches-abu-dhabi-healthcare-information-and-cyber-security
- DoH Abu Dhabi, ADHICS FAQ (seen in search results only, not read in full): https://www.doh.gov.ae/-/media/Feature/Aamen/ADHICS-FAQ.ashx
- UAE Government portal, data protection laws: https://u.ae/en/about-the-uae/digital-uae/data/data-protection-laws
- Malaffi home page: https://malaffi.ae
- Malaffi resources: https://malaffi.ae/resources
- Malaffi, facilities required to connect: https://www.malaffi.ae/providers/facilities-required-to-connect/
- Malaffi policies: https://www.malaffi.ae/what-is-malaffi/policies/
- Microsoft Learn, list of Azure regions: https://learn.microsoft.com/azure/reliability/regions-list

Vendor statements:

- Core42, Sovereign Public Cloud brochure (April 2026): https://www.core42.ai/resources/whitepapers/sovereign-public-cloud-brochure-download

Secondary (law firms, trade body, press):

- Simmons & Simmons, UAE Personal Data Protection Law FAQs (6 December 2021): https://www.simmons-simmons.com/en/publications/ckwugz8lu16rs0a44qvbqcbz9/uae-personal-data-protection-law-faqs
- Trowers & Hamlins, ICT health data law (November 2021): https://www.trowers.com/insights/2021/november/ict-health-data-law
- US International Trade Administration, UAE regulations limit cross-border health data flows (22 January 2024): https://www.trade.gov/market-intelligence/united-arab-emirates-regulations-limit-cross-border-health-data-flows
- Baker McKenzie, Health Data Law, permitted transfers (seen in search results only): https://insightplus.bakermckenzie.com/bm/data-technology/united-arab-emirates-health-data-law-permitted-transfers-of-health-data
- Healthcare IT News, Abu Dhabi Health Data Services and Orion Health (launch coverage; seen in search results only): https://www.healthcareitnews.com/news/asia/abu-dhabi-health-data-services-and-orion-health-partner-deliver-first-hie-middle-east

Not retrieved, needed for verification: the PDPL and Federal Law No. 2 of 2019 texts and Ministerial Decision 51 of 2021 on the UAE legislation portal; ADHICS V2 (2024) and the DoH Standard on Patient Healthcare Data Privacy; the DoH Policy on the Abu Dhabi Health Information Exchange; the Responsible AI Standard V1 and DoH Circular 2026/10 are not in this list: both were read first-hand (Circular 2026/10 for [evidence.md](../evidence.md) section 8c).
