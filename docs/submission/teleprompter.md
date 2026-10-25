# Teleprompter

The words to say in the live demo, and nothing else. A timestamp marks every 15 seconds; the click cues are in square brackets and are not spoken. The full script, with the exact labels on screen, the numbers' sources and the recovery table, is [demo-script.md](demo-script.md). All data shown is synthetic. Pace: 2.3 words a second, so a 15-second block holds at most 35 words and every block is written to fit it. The 731 spoken words are about 5 minutes 20 seconds of speech; the last block starts at 6:00 and ends by 6:15, which leaves the clicks and the clock waits inside the 6:30 limit. Read it calmly: the pauses the clicks need are in the slack.

**0:00** [Title slide: 30.5%.]
In Abu Dhabi, an audit of positive bowel screening tests found that only thirty point five percent of patients ever had the follow-up colonoscopy. Nobody decided to lose them. Everyone assumed someone else had it.

**0:15** [Product name over the worklist.]
The dangerous failure is silence. This is an accountability and closure engine, not a diagnostic tool. Every positive result gets a named owner, a clock, an audit trail and a clinician-signed closure. Synthetic data.

**0:30** [Open the named patient's case: "Owner accepted", Dr. Huda Rahman, the timeline with the Automated chips.]
A positive result for our synthetic patient arrived. The system acknowledged it and named her owner, Doctor Rahman, by itself. She accepted, and the first message went out on its own: no draft, nobody approving.

**0:45** [Point at the amber banner. Terminal: docker compose stop scheduler.]
The amber banner is the honest part: no clinician has signed off those messages, so the demo runs on a placeholder and says so. Now the thing that runs every clock. I am killing it.

**1:00** [Role clinician. Channel used: Phone call. Record patient contacted.]
The owner phones the patient. Nobody answers, so the patient never acknowledges.

**1:15** [Role supervisor. Advance clock (days) 15, Advance. Stay on the case page.]
Eleven working days pass. The limit was five, and the patient never answered. With the scheduler dead, nothing tells anyone. This is what silence looks like: a case that looks fine and is not.

**1:30** [Second terminal: run the closure property test.]
While we wait for the watchdog: this test throws two thousand random event sequences at the engine on every run. Not one reaches closed without evidence that exists and a human sign-off.

**1:45** [Let the test finish; keep its output on screen.]
Three things a machine can never do here: close a case, approve a message, change consent. The decision matrix lists every event, who may emit it, and the test that proves it.

**2:00** [Glance at the dashboard tab.]
The watchdog notices a stopped scheduler within about a minute and a half.

**2:15** [Dashboard: the red banner, The scheduler is down. Terminal: docker compose start scheduler. The banner clears.]
There it is: the scheduler is down. Restart it, and the alert clears.

**2:30** [Case page: the overdue chip, Level 2 of 3, the timeline.]
The missed overdue flag, the reminder to the owner and the supervisor's escalation appear now, once each, in order, and the automatic reminder goes to the patient. Nothing was lost, nothing was skipped.

**2:45** [Role supervisor. Accept escalation.]
An escalation is a person, not a log line. The supervisor accepts, and that stops the ladder. Nothing here is fire-and-forget.

**3:00** [Worklist, open the case at Confirmatory completed. Role specialist. Evidence reference: Procedure/colonoscopy-0001. Sign off and close case.]
Now the other half. This case already had its colonoscopy, prepared earlier. Closure needs evidence that exists in the FHIR store, so I type one that does not. Refused. That refusal is the point.

**3:15** [Agency tab. Role supervisor. Run agency now.]
Behind the worklist, a supervisor agent, five department heads and fourteen specialists do the preparation. One run reads all twenty-two open cases.

**3:30** [Scroll the run: Quality and Operations, Evidence and Closure. Role specialist. Needs a person.]
Every overdue case gets a plain explanation, the evidence is looked up, the closure pack is assembled. Look at the inbox: one item, and it is a human decision. Agents prepare. People decide.

**3:45** [Click Open case. Record colonoscopy result.]
I open it from the inbox and record the real procedure.

**4:00** [Sign off and close case. Heading Closed. Timeline: Case closed (signed). Point at Reopen case.]
Signed with the clinician's own identity, and closed. No automated chip. And even a real record that says the colonoscopy was incomplete is refused: closure needs a complete one.

**4:15** [Dashboard. Role supervisor. The Human effort card.]
The dashboard counts human actions against automated actions. The minutes are assumed, not measured, and on a handful of demo cases they flatter the system. The real figure is on the numbers slide.

**4:30** [Case page. Role coordinator. Record patient reply: WhatsApp, the Arabic text. Record reply.]
One more cameo. A patient writes back in Gulf Arabic: God willing, I will come on Sunday. A small offline model, running on this laptop, reads it as a confirmation, and no person is needed.

**4:45** [Point at Read as confirm. Optional: record STOP, point at the amber banner.]
An opt-out like STOP is caught first by a plain keyword rule, before any model, and always goes to a person. So does anything uncertain. It can only flag, never close, send or change consent.

**5:00** [Slide: the results table.]
Synthetic simulation, before capacity limits. Colonoscopy within one hundred eighty days: fifty-one percent today, sixty-seven with the system, a ratio about one point three one. We validated the ratio, not speed, so we claim none.

**5:15** [Same slide.]
Cases closed with evidence and a signature: fifty-three percent today, sixty-eight percent with the system. Lost without anyone told: forty-five percent today, zero where the programme reaches, twenty-one percent programme-wide, under a reach assumption.

**5:30** [Slide: labour.]
Labour: thirty-seven percent fewer staff minutes per handled case, twenty percent across the programme. Our target was forty percent. It is not met in realistic scenarios; only the upper bound, a mechanism ceiling, crosses it.

**5:45** [Slide: the pilot ask.]
The ask: one programme, two providers, ninety days, shadow mode first. For the first thirty days the engine watches and sends nothing. Judges can replay all of this alone: the Judge tour button.

**6:00** [Same slide.]
One number must stay at zero: cases closed without evidence. If it moves, we stop. A clinician and a native Arabic speaker review the wording and the rules before any pilot. Thank you.
