# Behaviour simulator

Status: Week 1 placeholder. No simulator code yet.

The simulator will drive synthetic cases through the pathway using a seeded random
generator and the injectable sim clock, so that a 45-day pathway runs in minutes and
every number in the documentation can be regenerated with `make evaluate`.

It will model, per case: how long staff take to acknowledge, whether the patient is
reachable (wrong contact details), whether the patient attends (no-show by channel),
and whether the referral is rejected. A baseline arm (no ownership, no clocks) is
compared with the orchestrated arm.

## Parameters

Values live in [`params.yaml`](params.yaml). Every parameter must carry a source
before the submission; until then it is declared `assumed`.

| parameter | value | source | status |
|---|---|---|---|
| acknowledgement_delay_days | 2 | TODO | assumed |
| no_show_rate_by_channel.sms | 0.30 | TODO | assumed |
| no_show_rate_by_channel.phone_call | 0.20 | TODO | assumed |
| no_show_rate_by_channel.letter | 0.40 | TODO | assumed |
| referral_rejection_rate | 0.05 | TODO | assumed |
| wrong_contact_rate | 0.08 | TODO | assumed |
