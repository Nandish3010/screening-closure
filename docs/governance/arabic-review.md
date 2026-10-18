# Arabic review record

> **AI-assisted Arabic review (2026-10-18). Not a native-speaker human review. H2 pending.**
>
> Written by an AI model (Claude Opus 5.5) taking the role of a Gulf Arabic health-communication writer. It is a
> pre-review that tidies the drafts so the native speaker's 30 minutes go on judgement, not typos. It does not
> close H2 or T-E14. Every Arabic text stays labelled "Arabic draft, unreviewed" until a native speaker signs off.
> All data in this project is synthetic. Nothing here is clinical advice.

Scope: the 70 Arabic texts and the Arabic forbidden-phrase list in [the Arabic review pack](../review/arabic-pack.md),
taken from [strings.ts](../../web/src/i18n/strings.ts) (W01 to W49), [ar.yaml](../../api/app/ai/templates/ar.yaml)
(T01 to T18), `web/src/api/mockAdapter.ts` (D01 to D03) and [forbidden_phrases.yaml](../../api/app/ai/forbidden_phrases.yaml).
Branch `cut-reviews`. The pack was regenerated after the edits (`make review-packs`), so it shows the new wording.

## 1. Summary

| Item | Result |
|---|---|
| Texts reviewed | 70 (49 page, 18 templates, 3 demo drafts) |
| Texts changed | 43 (25 page strings, all 18 templates); the 3 demo drafts have recommended wording only (section 7), because `mockAdapter.ts` was outside this review's files |
| Forbidden phrases | 53 Arabic entries kept unchanged, 16 added (69 in all) |
| Placeholders | All kept: `{owner_name}`, `{next_step}`, `{appointment_date}`, and `{name}`, `{role}` on the page |
| Review markers | Every `TODO native-speaker review (H2)` marker kept |
| SMS limit | Every SMS fits 320 characters, also with 40-character worst-case field values (largest: reminder SMS, 255) |
| Non-disclosure | No text names a finding, a disease or a judgement; the forbidden list finds nothing in any of our texts |
| Tests | `test_ai_templates.py`, `test_ai_validate.py`, `test_ai_validate_hardening.py`: 265 passed. `make web-test`: 188 passed. `make test-review-packs`: 19 passed. `make review-packs-check`: up to date |

## 2. Terminology chosen

One term per idea, applied in every text. Grammar still inflects it (article, possessive, first person).

| Concept | Chosen Arabic | Replaces | Why |
|---|---|---|---|
| Colonoscopy | تنظير القولون, named once on the patient page (W16) beside the friendly term; never in messages | (no change) | The term patients will hear from the clinic. Messages keep it out (tests forbid تنظير in templates) and use `{next_step}` |
| Camera test (the colonoscopy, in plain words) | فحص بالكاميرا (definite: الفحص بالكاميرا) | فحصي in W40 | Matches the default `{next_step}` value in `templates.py`, so the page and messages say the same thing. See question 1 on منظار |
| Screening test | فحص الكشف المبكر | فحصك in first mentions (W01, W04, W07, T01, T02, T04 to T08, T13 to T15) | The English says "screening". Naming it tells the stool test apart from the camera test, which is also a فحص |
| Care team | فريق رعايتك (first person: فريق رعايتي) | فريق الرعاية (8 strings) | The English always says "your care team". One possessive form, written the same for men and women |
| Appointment | موعد | (no change) | Already consistent |
| Result | نتيجة (نتيجتك, نتيجتي) | (no change) | Already consistent |
| Care owner (the responsible person) | الشخص المسؤول عن رعايتك, followed by a colon and the name | المسؤول عن رعايتك هو, المسؤول عن متابعة حالتك, من يتابع حالتك | The colon removes هو before a woman's name (الدكتورة سارة خان). الشخص keeps it neutral |
| "Looks after you" (page) | يتابعك | يتابع حالتك | حالتك reads as "your condition" and hints at an illness. W18 heading is now من يتابعك |
| "Your case" (page) | ملفك | حالتك | Same reason; ملف is the everyday Gulf word for a patient's file at a clinic |
| Care coordinator | منسق الرعاية (indefinite منسق رعاية in W20) | (no change) | Already consistent |

## 3. Conventions chosen

**Diacritics and tanween.** No vowel marks except: tanween fath written on the alef (ـاً: شكراً, وجهاً, مؤخراً, قريباً),
the kasratan of جارٍ, and a shadda only where a word would otherwise be misread (سنعيّن). The page had four cases of
ـًا; they now use ـاً like the templates, which is the form in UAE government and press writing. The validator
removes all marks before matching, so this choice changes no check.

**Punctuation.** Arabic comma ، and Arabic question mark ؟. Sentences end with "." (Arabic has no separate full stop).

**Greetings.** مرحباً in SMS and WhatsApp. Letters now open with تحية طيبة، instead of السلام عليكم، so the greeting is
neutral for every reader of Arabic, including non-Muslim and expatriate readers; it is the usual opening of a formal
Arabic letter in the UAE. Closing unchanged: مع تحياتنا، فريق رعايتك.

**Digits: Western digits (0 to 9) everywhere, including dates and times in SMS.** Reasons:

1. The validator compares values as strings. `_collapse()` uses NFKC, which does not turn Arabic-Indic digits
   (٠ to ٩) into 0 to 9. If the date formatter wrote ٣ نوفمبر ٢٠٢٦ and a model draft wrote 3 نوفمبر 2026 (or the
   reverse), the draft would fail `missing_appointment_date` and the value would not be stripped before the
   digit-run check. One digit system end to end keeps those checks exact. The PII check already folds every
   digit script to 0 to 9 (`_ascii_digits`), so Western digits are its native form.
2. Expatriate readers of Arabic from South Asia and Iran use the Extended Arabic-Indic digits (۴ ۵ ۶), which differ
   from the Arabic-Indic ones (٤ ٥ ٦). Western digits are read the same by all of them, and by everyone who reads
   the English message.
3. UAE health and government SMS commonly use Western digits in Arabic text, so patients are used to them.
4. There is no length cost either way: an Arabic SMS is sent as UCS-2 whichever digits it uses.

Also: the formatter should write the month as a word (الثلاثاء 3 نوفمبر 2026), never 03/11/2026. A word avoids the
day-month order question for expatriate readers, and a numeric date followed by a time (03/11/2026 10) is a run of
ten digits that `_LONG_DIGITS` would flag as personal data in a model draft.

**Right-to-left and bidi marks.** The page already wraps the owner's name in a first-strong isolate (U+2068 to U+2069).
The templates cannot do the same: `validate.py` refuses every Unicode format character (category Cf, which includes
FSI, PDI, RLM and LRM) as a way to hide words from the matcher (SEC-1), and every template must pass `validate()`.
So the templates are now written to display correctly without marks:

- Every line starts with an Arabic word. Phones choose each paragraph's direction from its first strong letter,
  so a line that started with a Latin `{owner_name}` would turn left-to-right.
- `{owner_name}` is always followed by an Arabic comma, a full stop or Arabic text, never by a digit, a bracket or
  another placeholder, so a Latin name stays one left-to-right run inside the Arabic sentence. In the owner
  lines it is the last item of its sentence (الشخص المسؤول عن رعايتك: {owner_name}.).
- `{appointment_date}` follows يوم and is never next to `{owner_name}`. Its digits sit between Arabic words.
- If marks are still wanted, they would have to be added by the sending adapter after validation, which changes
  the SEC-1 rule; that is an engineering decision, not a wording one (question 9).

**Gender.** No form that changes with the patient's gender or the owner's gender:

- `{owner_name} will call you` was ستتلقى اتصالاً (masculine "you"); it is now سيصلك اتصال من {owner_name}: the verb
  agrees with اتصال, the pronoun ك is written the same for a man and a woman, and the owner's gender does not matter.
- تحتاج became بحاجة; افتح ... لترى became يرجى فتح ... لرؤية; ستحصل became سيصلك; أنت غير متصل became لا يوجد اتصال;
  تطرح عليه (masculine "them") was dropped from the owner lines.
- Job titles (طبيبك, منسق الرعاية, الأخصائي) stay in the generic masculine, as is normal in Modern Standard Arabic;
  the role tests also depend on them. See question 6.

**Readability (grade 6).** No Arabic readability formula is in the project. As a proxy, sentence length in the
templates fell from at most 16 words (mean 6.7) to at most 13 (mean 6.0). The long SMS sentence "result ready, and the
team needs to talk, which is X" is split in two. Words kept on purpose: بخصوص, مؤخراً, لإبلاغك (common in clinic letters);
question 13 asks whether they are plain enough.

## 4. SMS length after the edits

Characters with the sample values, and with every placeholder filled by 40 characters (the loader's worst case).
Arabic SMS travels as UCS-2: 70 characters fit one SMS, 67 per part when split, so 320 characters is 5 parts.

| ID | SMS | Sample | Worst case | Limit | Parts (sample) |
|---|---|---|---|---|---|
| T01 | result_notification | 175 | 225 | 320 | 3 |
| T04 | reminder | 205 | 255 | 320 | 4 |
| T05 | reminder, with date | 170 | 238 | 320 | 3 |
| T10 | appointment_confirmation | 150 | 218 | 320 | 3 |
| T13 | escalation_notice | 214 | 264 | 320 | 4 |
| T16 | callback_ack | 167 | 217 | 320 | 3 |

No SMS failed to fit. WhatsApp (largest 330 of 600 worst case) and letters (largest 465 of 1200) are far inside.

## 5. Patient page strings (W01 to W49)

Keep: the Arabic is accurate, plain and neutral as it was. Changed: the new Arabic is in `strings.ts`, with a
`// AI Arabic review 2026-10-18` comment.

| ID | Key | Status | New Arabic | Reason |
|---|---|---|---|---|
| W01 | appTitle | changed | نتيجة فحص الكشف المبكر | Screening term |
| W02 | language | keep | | |
| W03 | switchTo | keep | | |
| W04 | homeTitle | changed | نتيجة فحص الكشف المبكر | Screening term |
| W05 | homeBody | changed | يرجى فتح الرابط الذي أرسله لك فريق رعايتك لرؤية نتيجتك. يمكنك إضافة هذه الصفحة إلى الشاشة الرئيسية. | Gender-neutral; care-team term |
| W06 | resultHeading | keep | | |
| W07 | resultBody | changed | نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عنها وعن الخطوة التالية. | Screening term |
| W08 | explainInPerson | changed | سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف. | Tanween form; same sentence now in WhatsApp and letters |
| W09 | progressHeading | changed | أين وصلت الآن | أين أنت الآن reads as a place, not progress |
| W10 | progress[0] | keep | | |
| W11 | progress[1] | changed | فريق رعايتك جاهز | Care-team term |
| W12 | progress[2] | keep | | |
| W13 | progress[3] | keep | | |
| W14 | nextHeading | keep | | |
| W15 | nextSteps[0] | keep | | |
| W16 | nextSteps[1] | keep | | Names تنظير القولون once beside the friendly term |
| W17 | nextSteps[2] | changed | سيصلك موعد وخطوات سهلة للتحضير. | Gender-neutral |
| W18 | ownerHeading | changed | من يتابعك | Matches the English; no حالتك |
| W19 | ownerLine | changed | الشخص المسؤول عن رعايتك: then the name and role, then يمكنك طرح أي سؤال. | Care-owner term; three lines kept for bidi |
| W20 | ownerPending | changed | سنعيّن لك منسق رعاية قريباً. | Tanween form only |
| W21 | ownerByRole.primary_care_owner | changed | يتابعك طبيبك في الرعاية الأولية. يمكنك طرح أي سؤال. | No حالتك; no عليه (the doctor may be a woman) |
| W22 | ownerByRole.specialist_owner | changed | يتابعك طبيبك الأخصائي. يمكنك طرح أي سؤال. | As W21 |
| W23 | ownerByRole.coordinator | changed | يتابعك منسق الرعاية الخاص بك. يمكنك طرح أي سؤال. | As W21 |
| W24 | ownerByRole.supervisor | changed | يتابعك أحد كبار أعضاء فريق رعايتك. يمكنك طرح أي سؤال. | As W21 |
| W25 | ownerByRole.other | changed | يتابعك فريق رعايتك. يمكنك طرح أي سؤال. | As W21 |
| W26 | role: Primary care physician | keep | | |
| W27 | role: Care coordinator | keep | | |
| W28 | understand | keep | | |
| W29 | understandDone | changed | شكراً لك. سجلنا طلبك. سيتواصل معك فريق رعايتك. | Care-team term; tanween form |
| W30 | understandWait | changed | يمكنك الضغط على هذا الزر بعد أن يرسل لك فريق رعايتك رسالة. | Care-team term |
| W31 | dontUnderstand | keep | | |
| W32 | callbackDone | changed | شكراً لك. سيتصل بك أحد أفراد فريق رعايتك. | Care-team term; tanween form |
| W33 | actionFailed | changed | لم تنجح العملية. يرجى المحاولة مرة أخرى أو الاتصال بفريق رعايتك. | Care-team term |
| W34 | consentHeading | keep | | |
| W35 | channelLabel | keep | | |
| W36 | channels.phone | keep | | |
| W37 | channels.sms | keep | | |
| W38 | channels.whatsapp | keep | | |
| W39 | channels.letter | keep | | |
| W40 | shareLabel | changed | أسمح لفريق رعايتي بمشاركة نتيجتي مع الطبيب الأخصائي الذي سيجري لي الفحص بالكاميرا. | فحصي was ambiguous (screening or camera test) |
| W41 | withdrawButton | changed | أوقفوا كل تواصل معي | Pairs with W44 (يمكنكم التواصل معي) |
| W42 | withdrawHint | changed | إذا أوقفت التواصل، فلن يرسل لك فريق رعايتك رسائل ولن يتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك. | أحد الأشخاص sounded vague; ملفك, not حالتك |
| W43 | withdrawnNotice | changed | طلبت إيقاف التواصل. لن نرسل لك رسائل ولن نتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك. | ملفك, not حالتك |
| W44 | resumeButton | keep | | |
| W45 | saved | keep | | |
| W46 | saveFailed | keep | | |
| W47 | loading | keep | | |
| W48 | loadError | keep | | |
| W49 | offline | changed | لا يوجد اتصال بالإنترنت. هذه آخر معلومات حفظناها. | أنت غير متصل is masculine |

W42 now says "a member of your care team" where the English says "a person"; the English W43 says the same, so the
meaning is unchanged. Unknown owner roles are still shown as stored (English inside Arabic), inside the isolate.

## 6. Message templates (T01 to T18)

All 18 changed; the full text is in `ar.yaml` and section 5 of the regenerated pack. Each entry carries a
`# AI Arabic review 2026-10-18` comment.

| ID | Template | Status | Main changes |
|---|---|---|---|
| T01 | result_notification/sms | changed | فحص الكشف المبكر; sentence split; سيصلك اتصال من {owner_name} |
| T02 | result_notification/whatsapp | changed | As T01; paragraphs in the English order; same "explain" sentence as W08 |
| T03 | result_notification/letter | changed | تحية طيبة; paragraphs in the English order; owner line الشخص المسؤول عن خطوتك التالية في فريق رعايتك: {owner_name}. |
| T04 | reminder/sms | changed | As T01 |
| T05 | reminder/sms_with_date | changed | يوم {appointment_date}; الشخص المسؤول عن رعايتك: {owner_name}. |
| T06 | reminder/whatsapp | changed | As T01; paragraphs in the English order |
| T07 | reminder/whatsapp_with_date | changed | As T05 |
| T08 | reminder/letter | changed | تحية طيبة; as T06 |
| T09 | reminder/letter_with_date | changed | تحية طيبة; as T05 |
| T10 | appointment_confirmation/sms | changed | يوم before the date; لإبلاغك بالتفاصيل (بالتفاصيل alone was not idiomatic); owner line |
| T11 | appointment_confirmation/whatsapp | changed | As T10 |
| T12 | appointment_confirmation/letter | changed | تحية طيبة; as T10 |
| T13 | escalation_notice/sms | changed | فحص الكشف المبكر; one idea per sentence (نريد التأكد من ألا تتأخر خطوتك التالية.); call line |
| T14 | escalation_notice/whatsapp | changed | As T13 |
| T15 | escalation_notice/letter | changed | تحية طيبة; as T13 |
| T16 | callback_ack/sms | changed | بحاجة instead of تحتاج; owner line |
| T17 | callback_ack/whatsapp | changed | As T16 |
| T18 | callback_ack/letter | changed | تحية طيبة; as T16 |

Kept on purpose: تم تأكيد موعدك in the confirmation SMS and WhatsApp (the English says only "your appointment is on");
it adds nothing clinical and is what a Gulf reader expects in a confirmation. The escalation text stays calm: it says
a senior member "has been told", with no word of urgency.

## 7. Demo drafts (D01 to D03), recommended only

`web/src/api/mockAdapter.ts` was outside this review's files, so these are not applied. `mockAdapter.test.ts` and
`coordinator.test.tsx` expect تنظير القولون in D01, so it stays.

| ID | Status | Recommended Arabic |
|---|---|---|
| D01 | change recommended | نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية، وهي فحص بالكاميرا اسمه تنظير القولون. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد. |
| D02 | change recommended | تذكير من فريق رعايتك. نتيجة فحص الكشف المبكر جاهزة. لا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد. |
| D03 | change recommended | تم تأكيد موعدك. سيتواصل معك فريق رعايتك لإبلاغك بالتفاصيل. يرجى الاتصال بنا إذا تعذّر عليك الحضور. (لم تتمكن is masculine) |

## 8. Forbidden phrases (Arabic list)

Kept: all 53 entries. None matches any of our texts. The false rejections they can cause (طبيعي in "normal opening
hours", صيام in a Ramadan notice, حبوب meaning grains) cost only a template message, which is the safe direction.
لا تقلقي and لا تخافي are already caught by the suffix rule; the separate لا تقلقي entry is harmless.

Added: 16 entries, each with an example the tests check, each marked `# AI Arabic review 2026-10-18: added`.

| Id | Phrase (as stored) | Category | Why |
|---|---|---|---|
| `disease` | مرض | disease_term | Names an illness. Also blocks المرضى (patients) |
| `inflammation` | التهاب | disease_term | A finding the screening test cannot show |
| `haemorrhoids` | بواسير | disease_term | A guessed cause, often used to reassure |
| `it_is_fine` | لا باس | false_reassurance | "It is fine" |
| `god_willing_good` | ان شاء الله خير | false_reassurance | Natural Gulf phrase that reassures about the outcome |
| `may_god_heal` | يشفي | false_reassurance | الله يشفيك implies an illness |
| `healing` | شفاء | false_reassurance | Wishing recovery implies an illness |
| `critical` | حرج | clinical_judgement | حالة حرجة; also blocks لا حرج (no problem) |
| `chemotherapy_formal` | كيميائي | medication_or_treatment | Formal spelling beside the colloquial كيماوي |
| `must` | يجب | advice | "Must" without عليك |
| `must_colloquial` | لازم | advice | Colloquial "must" |
| `fasting_alt` | صوم | advice | الصوم, the other word for fasting |
| `blood` | دم | result_disclosure | Any mention of blood; the word boundary leaves مقدم, دمج, دمتم alone |
| `bleeding` | نزيف | result_disclosure | Names a finding |
| `negative_result` | سلبي | result_disclosure | States a result (and would be false here) |
| `not_sound` | غير سليم | result_disclosure | النتيجة غير سليمة states a result |

Considered and not added: سلامتك (also "for your safety" in preparation texts), بسيط (minor), ما عليك شر,
الحمد لله على السلامة. Question 11 asks the reviewer.

## 9. Questions a native speaker should still answer (H2)

1. **Camera test.** Is فحص بالكاميرا natural to Emirati patients, or do they say المنظار / فحص بالمنظار? If المنظار
   wins, the default `{next_step}` in `api/app/ai/templates.py` changes with it (W12, W16, W40 too).
2. **الكشف المبكر.** Public campaigns pair it with السرطان (الكشف المبكر عن سرطان ...). Does فحص الكشف المبكر alone
   hint at cancer for some readers? If so, is there a neutral word for "screening"?
3. **Greetings.** تحية طيبة in letters and مرحباً in SMS and WhatsApp: right for every reader, or would Emirati readers
   expect السلام عليكم from a health service?
4. **سيصلك اتصال من {owner_name}.** Natural, or does ستتلقى اتصالاً read better even though it is masculine?
5. **Colon lines.** Does الشخص المسؤول عن رعايتك: الدكتورة سارة خان. read as plain Arabic in an SMS, or like a form?
6. **Generic masculine titles.** Are طبيبك, منسق الرعاية, الطبيب الأخصائي acceptable when the person is a woman, and is
   the masculine-by-default second person acceptable where the text could not avoid it (none left in the templates)?
7. **Tanween.** Is ـاً (on the alef) the form you would choose, or ـًا?
8. **Digits.** Are Western digits right for older Emirati readers (section 3)? Are الثلاثاء and نوفمبر the words used?
9. **Display on a phone.** With no bidi marks allowed in templates, check one SMS and one WhatsApp message with a
   Latin owner name (Dr Sara Khan) and a date on iOS Messages, Google Messages and WhatsApp. If the order breaks,
   engineering must decide whether the sender may add isolates after validation.
10. **يتابعك / من يتابعك.** Warm and clear, or too familiar?
11. **Forbidden list.** Do the new entries block harmless sentences you would expect (مرض in المرضى, يجب, لازم, لا حرج)?
    What else should be blocked (سلامتك, بسيط, ما عليك شر)?
12. **أوقفوا كل تواصل معي.** Too abrupt for a button? Alternative: إيقاف التواصل معي.
13. **Plain words.** Are بخصوص, مؤخراً, لإبلاغك, أحد كبار أعضاء فريق رعايتك plain enough for grade 6 and for South Asian
    and other expatriate readers of Arabic?
14. **الأخصائي.** Keep the common Gulf spelling, or الاختصاصي?
15. **Escalation.** Does تم إبلاغ أحد كبار أعضاء فريق رعايتك sound alarming?

## 10. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| AI-assisted pre-review | Claude Opus 5.5 (AI) | 2026-10-18 | Changes applied as above; not a sign-off |
| Native Arabic speaker (H2) | | | Pending |
