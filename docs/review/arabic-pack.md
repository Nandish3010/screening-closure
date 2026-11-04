# Arabic review pack (H2)

> Generated file. Do not edit by hand. Regenerate with `make review-packs` (tools/review/build_packs.py).
> Built from pathway `colorectal_fit` v0.3, template set v3, forbidden-phrase list v1.
> For: a native Arabic speaker, 30 minutes
> All data in this project is synthetic. Nothing here is clinical advice or a claim of real-world performance.

## 0. How to use this pack in 30 minutes

| Minutes | Section | What to do |
|---|---|---|
| 3 | 1 and 2 | Read what the texts are for and the rules they follow. |
| 5 | 3 Conventions, terminology | Look at the terminology table: which wording should win? |
| 12 | 4 and 5 The strings | Read each Arabic text beside its English. Mark what you would change. |
| 5 | 6 Forbidden phrases | Is the Arabic list right and complete? |
| 5 | 7 and 8 Checklist, feedback table | Tick the checklist and fill the feedback table. |

There are 179 Arabic texts (every one is also in English, with the same ID) and 147 Arabic forbidden-phrase entries. Every Arabic text in the prototype's sources is in this pack. All of it was written by the engineering team and has not been reviewed by a native speaker. Please correct freely; suggest better wording in the feedback table.

**New, please read first (H2, needs a native Arabic speaker).** The mobile patient app and the privacy and consent centre added texts that have had no review of any kind, not even the earlier AI-assisted pass: My appointment and calendar (W53 to W65), What has happened (status timeline) (W95 to W109), Privacy centre: who looked at my record, how we use your data, this phone (W129 to W151). Also new in the older groups: the per-pathway wording of the next step (bowel, cervical, long-term conditions) and the role names. The privacy texts matter most: they tell the patient how to stop contact, who looked at the record and how the data is used.

## 1. What these texts are for

A patient in Abu Dhabi has had a screening stool test (FIT) and the care team has the result. The texts below tell the patient, in Arabic or English, that the result is ready and the care team needs to talk with them, who in the care team is responsible, and what the next step is, and let them say "I understand, please contact me" or "I don't understand, call me" (a person will call). They never say what the screening found: the care team explains the result in person or by phone.

Hard rules the Arabic must keep:

- No text states or hints at what the screening found (IFHAS: abnormal results are communicated with the participant present). No "blood", no "positive", no "abnormal", no diagnosis, no disease name.
- No reassurance ("don't worry"), no judgement of urgency, no advice beyond the next step. The care team contacts the patient.
- Every message names the care owner and the next step.
- A person approves every message before it is sent.

## 2. Where each text appears and its limits

| Source | Where | Reading level | Length limit |
|---|---|---|---|
| Patient page (IDs W01 to W155) | Web app at `/patient`, Arabic with right-to-left layout | About grade 6: short sentences, everyday words. | None on the page; short lines for a phone screen |
| Messages (IDs T01 to T21) | SMS, WhatsApp or letter, chosen from the patient's preferred channel | About grade 6: short sentences, everyday words. | SMS 320, WhatsApp 600, letter 1200 characters |
| Demo drafts (IDs D01 to D03) | Coordinator app, demo data only | About grade 6: short sentences, everyday words. | None |

Placeholders in braces are filled in by the system: `{owner_name}` (the care owner, for example الدكتورة سارة خان), `{next_step}` (supplied in Arabic, default فحص بالكاميرا), `{appointment_date}` (for example الثلاثاء 3 نوفمبر 2026), `{name}` and `{role}` on the patient page.

## 3. Conventions in use

**Right-to-left.** The patient page sets `lang="ar"` and `dir="rtl"` on the page and the document when Arabic is chosen (English: `ltr`). Layout uses logical properties (start and end, not left and right), and the font list includes Noto Sans Arabic. The care owner's name is wrapped in a first-strong isolate (U+2068 to U+2069) so a Latin name does not scramble the neighbouring Arabic. SMS, WhatsApp and letter templates are plain text with no direction marks: a Latin `{owner_name}` inside a right-to-left sentence may display out of order on some phones. Please check this on a device.

**Digits.** Western digits (0 to 9) are used everywhere today, for example "الثلاثاء 3 نوفمبر 2026". Arabic-Indic digits (٠ to ٩) are not used in any text. The system converts them only when checking for personal data (a long run of digits is blocked in either form). Which style should patients see? The appointment-date formatter is not built, so this is still open.

**Dates and names.** The sample date uses Gregorian month and weekday names in the Gulf style (نوفمبر, الثلاثاء), not the Levantine month names. Titles such as "الدكتورة" are part of the owner's name, supplied by the clinic.

**Orthography we noticed.** The patient page and demo drafts write tanween as ـًا (for example تشخيصًا): 0 cases there against 16 of the other form. The templates write ـاً (for example دماً): 16 cases against 0 of the other form. The Arabic comma ، appears 48 times (letter greetings and closings); sentences end with a Latin full stop. Choose one convention.

**Terminology we use today.** Counts are generated from the texts below. Where one idea has several wordings, please choose one.

| Concept | Arabic wording in use, with the strings that contain it |
|---|---|
| colonoscopy | تنظير القولون : 4 strings (W38, W53, W54, D01) |
| camera test (friendly term) | فحص بالكاميرا : 6 strings (W14, W38, W40, W54, W116, D01)<br>الفحص بالكاميرا : 3 strings (W14, W40, W116)<br>فحص متابعة بالكاميرا : not used |
| screening test | فحص الكشف المبكر : 12 strings (W01, W07, W10, T01, T02, T03, T04, T06 and 4 more) |
| test (generic) | فحصك : 4 strings (W53, T03, D01, D02)<br>الفحص : 3 strings (W14, W40, W116) |
| care team | فريق الرعاية : 2 strings (W75, W77)<br>فريق رعايتك : 69 strings (W08, W10, W11, W13, W17, W21, W25, W30 and 61 more)<br>فريق رعايتي : 1 string (W116) |
| care coordinator | منسق الرعاية : 3 strings (W74, W80, W84)<br>منسق رعاية : 1 string (W68) |
| care owner (the responsible person) | المسؤول عن رعايتك : 13 strings (W67, T05, T07, T09, T10, T11, T12, T16 and 5 more)<br>المسؤول عن متابعة حالتك : not used<br>من يتابع حالتك : not used |
| result | نتيجة : 18 strings (W01, W07, W10, W12, W16, W20, W24, T01 and 10 more)<br>نتيجتك : 10 strings (W08, W11, W37, W41, W45, W49, W97, W98 and 2 more)<br>نتيجتي : 2 strings (W09, W116) |
| appointment | موعد : 38 strings (W26, W32, W33, W38, W39, W40, W42, W43 and 30 more) |

## 4. Patient page strings, by screen section

Source: `web/src/i18n/strings.ts` (catalogue and role labels). Mark any string you would change.

### 4.1 Page frame, menu and language

Title, language switch, bottom menu and start page.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W01 | Your screening result | نتيجة فحص الكشف المبكر | Main heading (h1) at the top of the patient page. |
| W02 | Language | اللغة | Not shown. Used in the accessible label of the language button, as "Language: <other language>". |
| W03 | العربية | English | Language button, top of the page. It names the other language in that language: the English column is the button on the English page, the Arabic column the button on the Arabic page. |
| W04 | Main menu | القائمة الرئيسية | Not shown. Accessible name of the bottom menu with two tabs. |
| W05 | My care | رعايتي | First tab of the bottom menu: the page with the result, the next step, the appointment and the status. |
| W06 | Privacy | الخصوصية | Second tab of the bottom menu: the privacy centre (choices, who looked at my record, how we use your data). |
| W07 | Your screening result | نتيجة فحص الكشف المبكر | Heading of the app start page (/patient) when no case has been opened on this device. |
| W08 | Open the link your care team sent you to see your next steps. You can add this page to your home screen. | يرجى فتح الرابط الذي أرسله لك فريق رعايتك لرؤية نتيجتك. يمكنك إضافة هذه الصفحة إلى الشاشة الرئيسية. | Text under that heading: how to open the result and add the page to the home screen. |

### 4.2 My result: what the result means

The first thing the patient reads.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W09 | My result | نتيجتي | Heading of the first card. |
| W10 | Your screening result is ready. Your care team needs to talk with you about it and about the next step. | نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عنها وعن الخطوة التالية. | Large paragraph under the heading. It says the result is ready and that the care team needs to talk with the patient. It states no finding. |
| W11 | Your care team will explain your result in person or by phone. | سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف. | Line under the paragraph: the care team explains the result in person or by phone (IFHAS: abnormal results are given with the participant present). |

### 4.3 Where you are now

Four-step journey strip, worded for each pathway.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W12 | Result received | وصلت النتيجة | Step 1 of 4 of the journey strip, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W13 | Care team ready | فريق رعايتك جاهز | Step 2 of 4 of the journey strip, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W14 | Camera test | الفحص بالكاميرا | Step 3 of 4 of the journey strip, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W15 | Doctor review | مراجعة الطبيب | Step 4 of 4 of the journey strip, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W16 | Result received | وصلت النتيجة | Step 1 of 4 of the journey strip, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W17 | Care team ready | فريق رعايتك جاهز | Step 2 of 4 of the journey strip, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W18 | Follow-up examination | فحص المتابعة | Step 3 of 4 of the journey strip, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W19 | Doctor review | مراجعة الطبيب | Step 4 of 4 of the journey strip, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W20 | Result received | وصلت النتيجة | Step 1 of 4 of the journey strip, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W21 | Care team ready | فريق رعايتك جاهز | Step 2 of 4 of the journey strip, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W22 | Family doctor visit | زيارة طبيب الأسرة | Step 3 of 4 of the journey strip, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W23 | Care plan | خطة الرعاية | Step 4 of 4 of the journey strip, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W24 | Result received | وصلت النتيجة | Step 1 of 4 of the journey strip, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W25 | Care team ready | فريق رعايتك جاهز | Step 2 of 4 of the journey strip, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W26 | Appointment | الموعد | Step 3 of 4 of the journey strip, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W27 | Doctor review | مراجعة الطبيب | Step 4 of 4 of the journey strip, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W28 | Where you are now | أين وصلت الآن | Heading of the journey strip. |

### 4.4 My next step

What the patient is told to do next, in each situation and for each pathway, with the one main button.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W29 | My next step | خطوتي التالية | Heading of the next-step card. |
| W30 | Your care team is getting ready to contact you. You do not need to do anything yet. | يستعد فريق رعايتك للتواصل معك. لا تحتاج إلى فعل شيء الآن. | Shown before the care team has contacted the patient. No button: nothing to do yet. |
| W31 | Your care team sent you a message. Tap the button when you have read it. | أرسل لك فريق رعايتك رسالة. اضغط على الزر بعد قراءتها. | Shown after the care team contacted the patient. The one main button (key understand) sits under it. |
| W32 | Your appointment is booked. Please check the date and add it to your calendar. | تم حجز موعدك. يرجى التأكد من التاريخ وإضافته إلى التقويم. | Shown once an appointment is booked. The main button is "Add to my calendar" (key addToCalendar). |
| W33 | Your appointment is done. A doctor from your care team will go over it with you. | تم الموعد. سيراجع معك طبيب من فريق رعايتك ما جرى. | Shown after the appointment has taken place and a doctor reviews it. States no finding. |
| W34 | Your care team has finished this step. Call your care team if you have a question. | أنهى فريق رعايتك هذه الخطوة. اتصل بفريق رعايتك إذا كان لديك سؤال. | Shown when the care team has finished the step. |
| W35 | You asked us to stop contact. You can allow contact again in Privacy. | طلبت إيقاف التواصل. يمكنك السماح بالتواصل من جديد في قسم الخصوصية. | Shown while the patient has stopped all contact; points to the Privacy tab. |
| W36 | Your care team is working on your next step. Call your care team if you have a question. | يعمل فريق رعايتك على خطوتك التالية. اتصل بفريق رعايتك إذا كان لديك سؤال. | Shown for any case that is being handled in a way the other lines do not describe (a referral being re-routed, a step reopened). Neutral: no finding. |
| W37 | Your care team will talk with you about your result. | سيتحدث معك فريق رعايتك عن نتيجتك. | Numbered step 1 of 3 in the plain explainer of the next step, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W38 | The next step is an appointment for a camera test called a colonoscopy. | الخطوة التالية هي موعد لفحص بالكاميرا اسمه تنظير القولون. | Numbered step 2 of 3 in the plain explainer of the next step, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W39 | You will get a date and clear steps to get ready. | سيصلك موعد وخطوات سهلة للتحضير. | Numbered step 3 of 3 in the plain explainer of the next step, in the wording of the bowel screening pathway (a camera test, colonoscopy). |
| W40 | Your care team is arranging your camera test. You will get a date soon. | يجهّز فريق رعايتك موعد الفحص بالكاميرا. سيصلك التاريخ قريباً. | Shown under "My next step" after the patient confirmed, while the care team arranges the appointment (bowel screening pathway (a camera test, colonoscopy)). |
| W41 | Your care team will talk with you about your result. | سيتحدث معك فريق رعايتك عن نتيجتك. | Numbered step 1 of 3 in the plain explainer of the next step, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W42 | The next step is an appointment for a follow-up examination. | الخطوة التالية هي موعد لفحص متابعة في العيادة. | Numbered step 2 of 3 in the plain explainer of the next step, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W43 | You will get a date and clear steps to get ready. | سيصلك موعد وخطوات سهلة للتحضير. | Numbered step 3 of 3 in the plain explainer of the next step, in the wording of the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W44 | Your care team is arranging your follow-up examination. You will get a date soon. | يجهّز فريق رعايتك موعد فحص المتابعة. سيصلك التاريخ قريباً. | Shown under "My next step" after the patient confirmed, while the care team arranges the appointment (cervical screening pathway (a follow-up examination; placeholder wording, H1)). |
| W45 | Your care team will talk with you about your result. | سيتحدث معك فريق رعايتك عن نتيجتك. | Numbered step 1 of 3 in the plain explainer of the next step, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W46 | The next step is a visit with your family doctor. | الخطوة التالية هي زيارة طبيب الأسرة. | Numbered step 2 of 3 in the plain explainer of the next step, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W47 | You will get a date and clear steps to get ready. | سيصلك موعد وخطوات سهلة للتحضير. | Numbered step 3 of 3 in the plain explainer of the next step, in the wording of the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W48 | Your care team is arranging your visit with your family doctor. You will get a date soon. | يجهّز فريق رعايتك موعد زيارتك لطبيب الأسرة. سيصلك التاريخ قريباً. | Shown under "My next step" after the patient confirmed, while the care team arranges the appointment (long-term conditions pathway (a visit with the family doctor; placeholder wording, H1)). |
| W49 | Your care team will talk with you about your result. | سيتحدث معك فريق رعايتك عن نتيجتك. | Numbered step 1 of 3 in the plain explainer of the next step, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W50 | The next step is an appointment. | الخطوة التالية هي موعد. | Numbered step 2 of 3 in the plain explainer of the next step, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W51 | You will get a date and clear steps to get ready. | سيصلك موعد وخطوات سهلة للتحضير. | Numbered step 3 of 3 in the plain explainer of the next step, in the wording of the any pathway the app does not know by name (a plain appointment). |
| W52 | Your care team is arranging your appointment. You will get a date soon. | يجهّز فريق رعايتك موعدك. سيصلك التاريخ قريباً. | Shown under "My next step" after the patient confirmed, while the care team arranges the appointment (any pathway the app does not know by name (a plain appointment)). |

### 4.5 My appointment and calendar

The booked appointment, in each pathway's own wording, and the calendar file (.ics) the patient can save.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W53 | Your camera test (colonoscopy) | فحصك بالكاميرا (تنظير القولون) | Heading of the appointment card, naming the appointment as the bowel screening pathway (a camera test, colonoscopy) does. |
| W54 | Camera test (colonoscopy) | فحص بالكاميرا (تنظير القولون) | Title of the calendar entry the patient saves, for the bowel screening pathway (a camera test, colonoscopy). |
| W55 | Your follow-up examination | فحص المتابعة في العيادة | Heading of the appointment card, naming the appointment as the cervical screening pathway (a follow-up examination; placeholder wording, H1) does. |
| W56 | Follow-up examination | فحص متابعة في العيادة | Title of the calendar entry the patient saves, for the cervical screening pathway (a follow-up examination; placeholder wording, H1). |
| W57 | Your visit with your family doctor | زيارتك لطبيب الأسرة | Heading of the appointment card, naming the appointment as the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1) does. |
| W58 | Visit with your family doctor | زيارة طبيب الأسرة | Title of the calendar entry the patient saves, for the long-term conditions pathway (a visit with the family doctor; placeholder wording, H1). |
| W59 | Your appointment | موعدك | Heading of the appointment card, naming the appointment as the any pathway the app does not know by name (a plain appointment) does. |
| W60 | Appointment | موعد | Title of the calendar entry the patient saves, for the any pathway the app does not know by name (a plain appointment). |
| W61 | My appointment | موعدي | Heading of the appointment card (shown once an appointment is booked). |
| W62 | Your care team will tell you the time. | سيخبرك فريق رعايتك بالوقت. | Under the date when the booking carries a date but no time. |
| W63 | Add to my calendar | أضف الموعد إلى التقويم | The main button once an appointment is booked. Saves a calendar file (.ics) on the phone. |
| W64 | This saves a small file on your phone. Open it to add the date to your calendar. | يحفظ هذا ملفاً صغيراً على هاتفك. افتح الملف لإضافة التاريخ إلى التقويم. | Small grey text under that button. |
| W65 | Booked by your care team. Please call your care team if you cannot come. | حجزه فريق رعايتك. يرجى الاتصال بفريق رعايتك إذا لم تتمكن من الحضور. | Description inside the calendar file the patient saves. Names no finding. |

### 4.6 Who is looking after you

The care owner by name and role, how to reach the care team, and role names.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W66 | Who is looking after you | من يتابعك | Heading of the care-owner card. |
| W67 | {name} ({role}) is looking after your care. You can ask them any question. | الشخص المسؤول عن رعايتك:<br>{name} ({role}).<br>يمكنك طرح أي سؤال. | Large text under that heading. {name} is the owner's name (Latin or Arabic script), {role} the role from the role names below. In Arabic the sentence is split over three lines so a Latin name does not scramble the line. |
| W68 | A person in your care team will be chosen to look after you soon. | سنعيّن لك منسق رعاية قريباً. | Shown instead of the line above while nobody has accepted the case. |
| W69 | How to reach your care team | كيف تتواصل مع فريق رعايتك | Small heading above the call button. |
| W70 | Call your care team | اتصل بفريق رعايتك | Button that starts a phone call to the care team. |
| W71 | Demo number. All data here is synthetic. | رقم تجريبي. كل البيانات هنا بيانات اصطناعية. | Small grey text under the call button: the number is a placeholder and the data is synthetic. |
| W72 | Primary care doctor | طبيب الرعاية الأولية | Role name shown with a staff member's name, for a primary care doctor. Also used in the list of who looked at the record. |
| W73 | Specialist doctor | الطبيب الأخصائي | Same, for the specialist doctor. |
| W74 | Care coordinator | منسق الرعاية | Same, for a care coordinator. |
| W75 | Senior member of the care team | أحد كبار أعضاء فريق الرعاية | Same, for a senior member of the care team. |
| W76 | Laboratory team | فريق المختبر | Same, for the laboratory team. |
| W77 | Care team | فريق الرعاية | Same, for any role not listed above. |
| W78 | Your primary care doctor is looking after your care. You can ask them any question. | يتابعك طبيبك في الرعاية الأولية. يمكنك طرح أي سؤال. | Shown instead of the owner line when the server sends the role but no name (the owner is the patient's primary care physician). |
| W79 | Your specialist doctor is looking after your care. You can ask them any question. | يتابعك طبيبك الأخصائي. يمكنك طرح أي سؤال. | Same, when the owner is the specialist who will do the test. |
| W80 | Your care coordinator is looking after your care. You can ask them any question. | يتابعك منسق الرعاية الخاص بك. يمكنك طرح أي سؤال. | Same, when the owner is a care coordinator. |
| W81 | A senior member of your care team is looking after your care. You can ask them any question. | يتابعك أحد كبار أعضاء فريق رعايتك. يمكنك طرح أي سؤال. | Same, when the case is with a senior member of the care team. |
| W82 | Your care team is looking after your care. You can ask them any question. | يتابعك فريق رعايتك. يمكنك طرح أي سؤال. | Same, for any role not listed above. |
| W83 | Primary care physician | طبيب الرعاية الأولية | Role shown in brackets after the owner's name in the line "Who is looking after you". The English role is the value stored in the case; unknown roles are shown as stored. |
| W84 | Care coordinator | منسق الرعاية | Role shown in brackets after the owner's name in the line "Who is looking after you". The English role is the value stored in the case; unknown roles are shown as stored. |

### 4.7 I understand / I don't understand / another date

The buttons and their confirmations.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W85 | Ask for another date | اطلب تاريخاً آخر | Secondary button once an appointment is booked: asks a person to call about another date (callback_requested, reason reschedule). |
| W86 | Thank you. A person from your care team will call you about a new date. | شكراً لك. سيتصل بك أحد أفراد فريق رعايتك بخصوص تاريخ جديد. | Green confirmation after that button. |
| W87 | I cannot come | لا أستطيع الحضور | Secondary button once an appointment is booked: asks a person to call because the patient cannot come (callback_requested, reason cannot_attend). |
| W88 | Thank you. A person from your care team will call you. | شكراً لك. سيتصل بك أحد أفراد فريق رعايتك. | Green confirmation after that button. |
| W89 | I understand, please contact me | فهمت، يرجى التواصل معي | The one main button after the patient was contacted (the patient has read the message and asks the care team to contact them). Disabled until the patient has been contacted. |
| W90 | Thank you. We saved this. Your care team will contact you. | شكراً لك. سجلنا طلبك. سيتواصل معك فريق رعايتك. | Green confirmation that replaces the button once the patient has acknowledged. |
| W91 | You can tap this button after your care team has sent you a message. | يمكنك الضغط على هذا الزر بعد أن يرسل لك فريق رعايتك رسالة. | Hint under the disabled button. |
| W92 | I don't understand, call me | لا أفهم، يرجى الاتصال بي | Second button, always present. Asks a person from the care team to call. |
| W93 | Thank you. A person from your care team will call you. | شكراً لك. سيتصل بك أحد أفراد فريق رعايتك. | Green confirmation after that button is pressed, or while a callback is pending. |
| W94 | That did not work. Please try again or call your care team. | لم تنجح العملية. يرجى المحاولة مرة أخرى أو الاتصال بفريق رعايتك. | Red banner if a button fails. |

### 4.8 What has happened (status timeline)

One plain sentence for each step the patient is allowed to see.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W95 | What has happened | ما الذي جرى | Heading of the status timeline. |
| W96 | Nothing yet. | لا شيء بعد. | Shown when there is nothing to list yet. |
| W97 | Your result was received. | وصلت نتيجتك. | Timeline line for the first step. Plain, no finding. |
| W98 | Your care team saw your result. | اطلع فريق رعايتك على نتيجتك. | Timeline line: the care team saw the result. |
| W99 | A member of your care team took charge of your care. | تولى أحد أفراد فريق رعايتك متابعتك. | Timeline line: a member of the care team took charge. |
| W100 | Your care team contacted you. | تواصل معك فريق رعايتك. | Timeline line: the care team contacted the patient. |
| W101 | You said you understood. | أخبرتنا أنك فهمت. | Timeline line: the patient said they understood. |
| W102 | Your appointment was booked. | تم حجز موعدك. | Timeline line: the appointment was booked. |
| W103 | Your appointment took place. | تم موعدك. | Timeline line: the appointment took place. |
| W104 | A doctor went over it. | راجع طبيب ما جرى. | Timeline line: a doctor went over it. States no finding. |
| W105 | Your care team finished this step. | أنهى فريق رعايتك هذه الخطوة. | Timeline line: the care team finished the step. |
| W106 | Your care team opened this step again. | فتح فريق رعايتك هذه الخطوة من جديد. | Timeline line: the care team opened the step again. |
| W107 | You changed your contact choices. | غيّرت اختياراتك للتواصل. | Timeline line: the patient changed contact choices. |
| W108 | You asked for a call. | طلبت أن نتصل بك. | Timeline line: the patient asked for a call. |
| W109 | We sent you a message. | أرسلنا لك رسالة. | Timeline line: a message was sent to the patient. |

### 4.9 Your choices (contact, language, sharing, stopping contact)

Channel, language, sharing, stopping and restoring contact, and their save status.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W110 | Your choices | اختياراتك | Heading of the choices section. |
| W111 | How may we contact you? | كيف يمكننا التواصل معك؟ | Legend of the group of contact-channel check boxes. Each box is on unless the patient turned it off (an opt-out default); changing one saves at once. |
| W112 | Phone call | مكالمة هاتفية | Label of the check box for phone calls. |
| W113 | Text message | رسالة نصية | Label of the check box for text messages. |
| W114 | WhatsApp | واتساب | Label of the check box for WhatsApp messages. |
| W115 | Letter | رسالة مكتوبة | Label of the check box for letters. |
| W116 | My care team may share my result with the specialist who will do my test. | أسمح لفريق رعايتي بمشاركة نتيجتي مع الطبيب الأخصائي الذي سيجري لي الفحص بالكاميرا. | Label of the check box that lets the care team share the result with the specialist. |
| W117 | Language for this app and your messages | لغة هذا التطبيق ورسائلك | Label of the English / Arabic choice inside the choices section. |
| W118 | Kept on this phone. We use it when you tap a button to reach your care team. | تُحفظ على هذا الهاتف. نستعملها حين تضغط على زر للتواصل مع فريق رعايتك. | Small grey text under it: the language is saved on the phone and sent with the buttons that reach the care team. |
| W119 | Stop all contact | أوقفوا كل تواصل معي | Button under the choices. Opens the confirmation below; confirming withdraws all consent to contact at once: the case is frozen for outreach and a person is asked to review it. Hidden after withdrawal. |
| W120 | If you stop contact, your care team will not message or call you. A person will check your case. You can still call your care team. | إذا أوقفت التواصل، فلن يرسل لك فريق رعايتك رسائل ولن يتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك. | Small grey text above that button. It says what stopping contact means and that a person will check the case. |
| W121 | You asked us to stop contact. We will not message or call you. A person on your care team will check your case. You can still call your care team. | طلبت إيقاف التواصل. لن نرسل لك رسائل ولن نتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك. | Yellow banner at the top of the choices section after the patient has stopped all contact. |
| W122 | Stop all contact? | هل تريد إيقاف كل تواصل؟ | Heading of the confirmation shown after pressing the stop button. |
| W123 | Your care team will not message or call you. A person will check your case. You can turn contact back on here at any time. | لن يرسل لك فريق رعايتك رسائل ولن يتصل بك. سيراجع أحد أفراد الفريق ملفك. يمكنك إعادة التواصل من هنا في أي وقت. | Text of that confirmation: what stopping means and that it can be undone here. |
| W124 | Yes, stop all contact | نعم، أوقفوا كل تواصل | Button that confirms stopping all contact. |
| W125 | No, keep contact | لا، أبقوا التواصل | Button that cancels and keeps contact. |
| W126 | You may contact me again | يمكنكم التواصل معي مرة أخرى | Replaces the stop button after withdrawal. Pressing it records that the patient may be contacted again. |
| W127 | Saved | تم الحفظ | Status line under the choices after a successful save. |
| W128 | We could not save this. Please try again. | لم نتمكن من الحفظ. يرجى المحاولة مرة أخرى. | Status line if saving the choices fails. |

### 4.10 Privacy centre: who looked at my record, how we use your data, this phone

Access log, the data panel and the notice about messages.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W129 | Privacy and choices | الخصوصية والاختيارات | Heading of the Privacy tab (h1). |
| W130 | You decide how we contact you. You can see who opened your record. You can change your mind at any time. | أنت من يقرر كيف نتواصل معك. يمكنك معرفة من اطلع على ملفك. يمكنك تغيير رأيك في أي وقت. | Text under that heading. |
| W131 | How we prepare messages | كيف نُعِدّ الرسائل | Heading of a short notice: how messages are prepared (software helps, a member of staff approves each one). |
| W132 | We use software to help prepare our messages to you. | نستعين ببرنامج حاسوبي في إعداد رسائلنا إليك. | First line of that notice. Says software helps prepare the messages. Says nothing about results. |
| W133 | A member of staff checks and approves each message before we send it. | يراجع أحد أفراد فريقنا كل رسالة ويوافق عليها قبل إرسالها. | Second line of that notice. A member of staff checks and approves each message before it is sent. |
| W134 | To speak to a person, press "I don't understand, call me". You can also call your care team. | للتحدث مع شخص، يرجى الضغط على «لا أفهم، يرجى الاتصال بي». ويمكنك أيضاً الاتصال بفريق رعايتك. | Third line of that notice. Names the button "I don't understand, call me" exactly as translated for the key dontUnderstand. |
| W135 | You can stop messages at any time. Use "Stop all contact" in "Your choices" below. | يمكنك إيقاف الرسائل في أي وقت. يمكنك ذلك من خلال «أوقفوا كل تواصل معي» في قسم «اختياراتك» أدناه. | Fourth line of that notice. Points to the "Stop all contact" button (key withdrawButton) in the choices section; the quoted names must match that key and the heading consentHeading. |
| W136 | How we use your data | كيف نتعامل مع بياناتك | Heading of the "how we use your data" panel. |
| W137 | This is a demonstration. All the information here is synthetic. It is not about a real person. | هذا عرض تجريبي. كل المعلومات هنا اصطناعية. وليست عن شخص حقيقي. | Panel line 1: the data is synthetic (this is a demonstration). |
| W138 | Software only prepares and sorts messages. It never decides anything about your care. | البرنامج الحاسوبي يُعِدّ الرسائل ويرتبها فقط. ولا يقرر شيئاً عن رعايتك. | Panel line 2: software only prepares and sorts messages; it never decides about care (the reply triage only flags or relays, the drafting model only drafts). |
| W139 | A person on your care team makes every decision about your care. | أحد أفراد فريق رعايتك هو من يتخذ كل قرار عن رعايتك. | Panel line 3: a person decides everything about care (closure always needs a physician's signature). |
| W140 | We keep only what we need. What you write to us is kept apart from your care record, and it can be erased. | نحتفظ بما نحتاج إليه فقط. ما تكتبه لنا يُحفظ بعيداً عن سجل رعايتك، ويمكن مسحه. | Panel line 4: data minimisation. What the patient writes is kept apart from the care record (patient_replies table) and can be erased. |
| W141 | Your care record lists steps, dates and roles. It does not hold names, phone numbers or free text. | يسرد سجل رعايتك الخطوات والتواريخ والأدوار. ولا يحتوي على أسماء أو أرقام هواتف أو نصوص حرة. | Panel line 5: data minimisation. The event log holds steps, dates and roles; its payload schemas hold no free text, and contact details are not logged. |
| W142 | When your case is finished, you can ask for your personal data to be erased. Two supervisors must agree. | عند انتهاء حالة رعايتك، يمكنك طلب مسح بياناتك الشخصية. ويجب أن يوافق مشرفان. | Panel line 6: erasure after the case is finished needs two supervisors (A32, ADR-025). |
| W143 | Who looked at my record | من اطلع على ملفي | Heading of the list of staff who opened the record. |
| W144 | Each time a member of staff opens your record, it is written here with their name, their role and the time. | في كل مرة يفتح أحد العاملين ملفك، يُكتب هنا اسمه ودوره والوقت. | Text under that heading. |
| W145 | Nobody on your care team has opened your record yet. | لم يفتح أحد من فريق رعايتك ملفك بعد. | Shown when nobody has opened the record yet. |
| W146 | We list each person once every 15 minutes, even if they look more than once. Computer systems that keep time or send reminders are not listed. | نسجل كل شخص مرة واحدة كل ١٥ دقيقة حتى لو نظر أكثر من مرة. الأنظمة الحاسوبية التي تحسب الوقت أو ترسل التذكيرات لا تظهر هنا. | Small grey text under the list: one line per person every 15 minutes; services are not listed. |
| W147 | We could not load this list. Please try again. | لم نتمكن من تحميل هذه القائمة. يرجى المحاولة مرة أخرى. | Shown if the list cannot be loaded. |
| W148 | On this phone | على هذا الهاتف | Heading of the section about what the phone stores. |
| W149 | To work without internet, this phone keeps the last information it loaded. It stays on this phone only. | ليعمل التطبيق بلا إنترنت، يحتفظ هذا الهاتف بآخر معلومات حمّلها. وتبقى على هذا الهاتف فقط. | Says the phone keeps the last loaded information so the app works without internet, and only on the phone. |
| W150 | Remove saved information from this phone | احذف المعلومات المحفوظة من هذا الهاتف | Button that removes the saved information from the phone. |
| W151 | Removed. Open the link from your care team again to load your page. | تم الحذف. افتح الرابط الذي أرسله فريق رعايتك من جديد لتحميل صفحتك. | Confirmation after that button. |

### 4.11 Loading, offline and errors

System messages.

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| W152 | Loading... | جارٍ التحميل... | Shown instead of the page while the case loads. |
| W153 | We could not load your information. Please try again. | لم نتمكن من تحميل معلوماتك. يرجى المحاولة مرة أخرى. | Shown if the case cannot be loaded and nothing is saved on the device. |
| W154 | You are offline. This is the last information we saved. | لا يوجد اتصال بالإنترنت. هذه آخر معلومات حفظناها. | Banner above the result when the device is offline. The last saved information is shown. |
| W155 | You are offline. You can read what is saved. You can send something when you are back online. | لا يوجد اتصال بالإنترنت. يمكنك قراءة المعلومات المحفوظة. يمكنك الإرسال عند عودة الاتصال. | Hint shown beside the buttons while the device is offline: they work again when the connection returns. |

## 5. Messages, by purpose

Source: `api/app/ai/templates/ar.yaml` beside `en.yaml`. Each purpose has an SMS, a WhatsApp message and a letter; the reminder also has a version with the appointment date. The Arabic is the default draft and the fallback if a model draft is rejected. The "Chars" column is the length with the sample values.

### 5.1 result_notification

First message to the patient once a result has an accepted care owner. States no finding: the care team will explain the result in person or by phone.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T01 | SMS | Your screening result is ready and your care team needs to talk with you about the next step. {owner_name} will call you, or you can reply to book a time. The next step is {next_step}. | نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية، وهي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 186 / 175, 320 |
| T02 | WhatsApp | Hello. This is a message from your care team.<br><br>Your screening result is ready and your care team needs to talk with you about the next step.<br><br>The next step is {next_step}. Your care team will explain your result in person or by phone.<br><br>{owner_name} will call you, or you can reply to book a time. | مرحباً، هذه رسالة من فريق رعايتك.<br><br>نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية.<br><br>الخطوة التالية هي {next_step}. سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف.<br><br>سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 298 / 280, 600 |
| T03 | Letter | Hello,<br><br>We are writing about the screening test you did recently.<br><br>Your screening result is ready and your care team needs to talk with you about the next step.<br><br>The next step is {next_step}. Your care team will explain your result in person or by phone.<br><br>{owner_name} will call you, or you can contact your care team to book a time.<br>{owner_name} is the person in your care team who is responsible for your next step.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>نكتب إليك بخصوص فحص الكشف المبكر الذي أجريته مؤخراً.<br><br>نتيجة فحصك جاهزة. يحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية.<br><br>الخطوة التالية هي {next_step}. سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف.<br><br>سيصلك اتصال من {owner_name}، أو يمكنك التواصل مع فريق رعايتك لحجز موعد.<br>الشخص المسؤول عن خطوتك التالية في فريق رعايتك: {owner_name}.<br><br>مع تحياتنا،<br>فريق رعايتك | 449 / 392, 1200 |

### 5.2 reminder

Reminder when the patient has not yet taken the next step. The dated variant is used once an appointment date exists.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T04 | SMS | Reminder from your care team. Your screening result is ready and your care team still needs to talk with you about the next step. {owner_name} will call you, or you can reply to book a time. The next step is {next_step}. | تذكير من فريق رعايتك. نتيجة فحص الكشف المبكر جاهزة. لا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية، وهي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 222 / 205, 320 |
| T05 | SMS with date | Reminder from your care team. Your appointment for {next_step} is on {appointment_date}. Your care owner is {owner_name}. Reply to this message to change the time. | تذكير من فريق رعايتك. موعدك لإجراء {next_step} يوم {appointment_date}. الشخص المسؤول عن رعايتك: {owner_name}. يمكنك الرد على هذه الرسالة إذا أردت تغيير الموعد. | 170 / 170, 320 |
| T06 | WhatsApp | Hello. This is a reminder from your care team.<br><br>Your screening result is ready and your care team still needs to talk with you about the next step.<br><br>The next step is {next_step}. {owner_name} will call you, or you can reply to book a time. | مرحباً، هذا تذكير من فريق رعايتك.<br><br>نتيجة فحص الكشف المبكر جاهزة. لا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية.<br><br>الخطوة التالية هي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 241 / 233, 600 |
| T07 | WhatsApp with date | Hello. This is a reminder from your care team.<br><br>Your appointment for {next_step} is on {appointment_date}.<br><br>Your care owner is {owner_name}. Reply to this message to change the time. | مرحباً، هذا تذكير من فريق رعايتك.<br><br>موعدك لإجراء {next_step} يوم {appointment_date}.<br><br>الشخص المسؤول عن رعايتك: {owner_name}. يمكنك الرد على هذه الرسالة إذا أردت تغيير الموعد. | 189 / 184, 600 |
| T08 | Letter | Hello,<br><br>This is a reminder from your care team.<br><br>Your screening result is ready and your care team still needs to talk with you about the next step.<br>The next step is {next_step}.<br><br>{owner_name} will call you, or you can contact your care team to book a time.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>هذا تذكير من فريق رعايتك.<br><br>نتيجة فحص الكشف المبكر جاهزة. لا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية.<br>الخطوة التالية هي {next_step}.<br><br>سيصلك اتصال من {owner_name}، أو يمكنك التواصل مع فريق رعايتك لحجز موعد.<br><br>مع تحياتنا،<br>فريق رعايتك | 289 / 264, 1200 |
| T09 | Letter with date | Hello,<br><br>This is a reminder from your care team. Your appointment for {next_step} is on {appointment_date}.<br><br>Your care owner is {owner_name}. Please contact your care team to change the time.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>هذا تذكير من فريق رعايتك. موعدك لإجراء {next_step} يوم {appointment_date}.<br><br>الشخص المسؤول عن رعايتك: {owner_name}. يرجى التواصل مع فريق رعايتك إذا أردت تغيير الموعد.<br><br>مع تحياتنا،<br>فريق رعايتك | 227 / 213, 1200 |

### 5.3 appointment_confirmation

Confirms a booked appointment (a camera test). Always carries the date. Names the appointment, never a disease or a finding.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T10 | SMS | Your appointment for {next_step} is on {appointment_date}. Your care team will contact you with the details. Your care owner is {owner_name}. | تم تأكيد موعدك لإجراء {next_step} يوم {appointment_date}. سيتواصل معك فريق رعايتك لإبلاغك بالتفاصيل. الشخص المسؤول عن رعايتك: {owner_name}. | 148 / 150, 320 |
| T11 | WhatsApp | Hello. This is a message from your care team.<br><br>Your appointment for {next_step} is on {appointment_date}.<br><br>Your care team will contact you with the details. Your care owner is {owner_name}. | مرحباً، هذه رسالة من فريق رعايتك.<br><br>تم تأكيد موعدك لإجراء {next_step} يوم {appointment_date}.<br><br>سيتواصل معك فريق رعايتك لإبلاغك بالتفاصيل. الشخص المسؤول عن رعايتك: {owner_name}. | 196 / 186, 600 |
| T12 | Letter | Hello,<br><br>We are writing to confirm your appointment for {next_step}. It is on {appointment_date}.<br><br>Your care team will contact you with the details.<br><br>Your care owner is {owner_name}.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>نكتب إليك لتأكيد موعدك لإجراء {next_step}. الموعد يوم {appointment_date}.<br><br>سيتواصل معك فريق رعايتك لإبلاغك بالتفاصيل.<br><br>الشخص المسؤول عن رعايتك: {owner_name}.<br><br>مع تحياتنا،<br>فريق رعايتك | 218 / 205, 1200 |

### 5.4 escalation_notice

Tells the patient a more senior member of the care team has been told, so the next step is not delayed.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T13 | SMS | Your screening result is ready. We want to make sure your next step is not delayed. A senior member of your care team has been told. The next step is {next_step}. {owner_name} will call you, or you can reply to book a time. | نتيجة فحص الكشف المبكر جاهزة. نريد التأكد من ألا تتأخر خطوتك التالية. تم إبلاغ أحد كبار أعضاء فريق رعايتك. الخطوة التالية هي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 225 / 214, 320 |
| T14 | WhatsApp | Hello. This is a message from your care team.<br><br>Your screening result is ready. We want to make sure your next step is not delayed. A senior member of your care team has been told.<br><br>The next step is {next_step}. {owner_name} will call you, or you can reply to book a time. | مرحباً، هذه رسالة من فريق رعايتك.<br><br>نتيجة فحص الكشف المبكر جاهزة. نريد التأكد من ألا تتأخر خطوتك التالية. تم إبلاغ أحد كبار أعضاء فريق رعايتك.<br><br>الخطوة التالية هي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك الرد على هذه الرسالة لحجز موعد. | 273 / 250, 600 |
| T15 | Letter | Hello,<br><br>Your screening result is ready. We want to make sure your next step is not delayed. A senior member of your care team has been told.<br><br>The next step is {next_step}. {owner_name} will call you, or you can contact your care team to book a time.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>نتيجة فحص الكشف المبكر جاهزة. نريد التأكد من ألا تتأخر خطوتك التالية. تم إبلاغ أحد كبار أعضاء فريق رعايتك.<br><br>الخطوة التالية هي {next_step}. سيصلك اتصال من {owner_name}، أو يمكنك التواصل مع فريق رعايتك لحجز موعد.<br><br>مع تحياتنا،<br>فريق رعايتك | 281 / 254, 1200 |

### 5.5 callback_ack

Reply when the patient pressed "I don't understand this". A person will call.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T16 | SMS | Thank you for telling us you need help to understand. A person from your care team will call you to explain. The next step is {next_step}. Your care owner is {owner_name}. | شكراً لإخبارنا أنك بحاجة إلى مساعدة لفهم الرسالة. سيتصل بك أحد أفراد فريق رعايتك ليشرح لك. الخطوة التالية هي {next_step}. الشخص المسؤول عن رعايتك: {owner_name}. | 173 / 167, 320 |
| T17 | WhatsApp | Hello. Thank you for telling us you need help to understand.<br><br>A person from your care team will call you to explain. The next step is {next_step}.<br><br>Your care owner is {owner_name}. | مرحباً، شكراً لإخبارنا أنك بحاجة إلى مساعدة لفهم الرسالة.<br><br>سيتصل بك أحد أفراد فريق رعايتك ليشرح لك. الخطوة التالية هي {next_step}.<br><br>الشخص المسؤول عن رعايتك: {owner_name}. | 182 / 177, 600 |
| T18 | Letter | Hello,<br><br>Thank you for telling us you need help to understand our message.<br><br>A person from your care team will call you to explain. The next step is {next_step}.<br><br>Your care owner is {owner_name}.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>شكراً لإخبارنا أنك بحاجة إلى مساعدة لفهم رسالتنا.<br><br>سيتصل بك أحد أفراد فريق رعايتك ليشرح لك. الخطوة التالية هي {next_step}.<br><br>الشخص المسؤول عن رعايتك: {owner_name}.<br><br>مع تحياتنا،<br>فريق رعايتك | 225 / 206, 1200 |

### 5.6 refusal_acknowledgement

After a patient's informed refusal is recorded (A33): the decision is recorded, their own doctor has been told, they can come back at any time. States no finding. Goes out as an approved draft through the existing message path; the system holds no GP channel yet, so telling the GP itself is the clinician's act.

| ID | Channel | English | Arabic | Chars en / ar, limit |
|---|---|---|---|---|
| T19 | SMS | Thank you for talking with your care team. Your decision about the next step ({next_step}) has been recorded and your own doctor has been told. You can change your mind at any time: contact {owner_name}. | شكراً لحديثك مع فريق رعايتك. تم تسجيل قرارك بشأن الخطوة التالية ({next_step}) وتم إبلاغ طبيبك. يمكنك تغيير رأيك في أي وقت. الشخص المسؤول عن رعايتك: {owner_name}. | 205 / 168, 320 |
| T20 | WhatsApp | Hello. This is a message from your care team.<br><br>Thank you for talking with us. Your decision about the next step ({next_step}) has been recorded, and your own doctor has been told.<br><br>You can change your mind at any time. Your care owner is {owner_name}. | مرحباً، شكراً لحديثك مع فريق رعايتك.<br><br>تم تسجيل قرارك بشأن الخطوة التالية ({next_step}) وتم إبلاغ طبيبك.<br><br>يمكنك تغيير رأيك في أي وقت. الشخص المسؤول عن رعايتك: {owner_name}. | 253 / 178, 600 |
| T21 | Letter | Hello,<br><br>Thank you for talking with your care team.<br><br>Your decision about the next step ({next_step}) has been recorded, and your own doctor has been told.<br><br>You can change your mind at any time. Please contact your care team and ask for {owner_name}.<br><br>Kind regards,<br>Your care team | تحية طيبة،<br><br>شكراً لحديثك مع فريق رعايتك.<br><br>تم تسجيل قرارك بشأن الخطوة التالية ({next_step}) وتم إبلاغ طبيبك.<br><br>يمكنك تغيير رأيك في أي وقت. يرجى التواصل مع فريق رعايتك. الشخص المسؤول عن رعايتك: {owner_name}.<br><br>مع تحياتنا،<br>فريق رعايتك | 280 / 236, 1200 |

### 5.7 Demo draft texts (coordinator app)

Draft text offered to the coordinator in the demo (mock) app, shown in both languages and stored in the `message_drafted` event. It stands in for a model or template draft. A person approves it before anything is "sent".

| ID | English | Arabic | Where it appears |
|---|---|---|---|
| D01 | Your screening result is ready and your care team needs to talk with you about the next step. Your care team will call you, or you can reply to book a time. The next step is a camera test called a colonoscopy. | نتيجة فحصك جاهزة، ويحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية، وهي فحص بالكاميرا اسمه تنظير القولون. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد. | Draft text offered to the coordinator in the demo (mock) app, shown in both languages and stored in the `message_drafted` event. It stands in for a model or template draft. A person approves it before anything is "sent". |
| D02 | Reminder from your care team. Your screening result is ready and your care team still needs to talk with you about the next step. Your care team will call you, or you can reply to book a time. | تذكير من فريق رعايتك. نتيجة فحصك جاهزة، ولا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد. | Draft text offered to the coordinator in the demo (mock) app, shown in both languages and stored in the `message_drafted` event. It stands in for a model or template draft. A person approves it before anything is "sent". |
| D03 | Your appointment is booked. Your care team will contact you with the details. Please call us if you cannot come. | تم تأكيد موعدك. سيتواصل معك فريق رعايتك بالتفاصيل. يرجى الاتصال بنا إذا لم تتمكن من الحضور. | Draft text offered to the coordinator in the demo (mock) app, shown in both languages and stored in the `message_drafted` event. It stands in for a model or template draft. A person approves it before anything is "sent". |

### 5.8 Priority reasons (staff worklist)

Short reasons shown to staff next to a case's priority tier (urgent, soon, routine) on the worklist and the case page; never sent to a patient. The thresholds behind them are placeholders pending a clinician (H1); please review only the Arabic wording. Source: the `priority` section of the files in `config/pathways/`.

| ID | English | Arabic | Rule(s) |
|---|---|---|---|
| P01 | Positive cancer screening result | نتيجة فحص إيجابية للكشف عن السرطان | cervical_hpv/positive_cancer_screen |
| P02 | Very high blood pressure | ارتفاع شديد في ضغط الدم | chronic_conditions/bp_very_high |
| P03 | High blood pressure | ارتفاع في ضغط الدم | chronic_conditions/bp_high |
| P04 | Raised blood pressure | ارتفاع طفيف في ضغط الدم | chronic_conditions/bp_raised |
| P05 | High HbA1c | ارتفاع في السكر التراكمي | chronic_conditions/hba1c_high |
| P06 | Mildly raised HbA1c | ارتفاع طفيف في السكر التراكمي | chronic_conditions/hba1c_raised |
| P07 | High fasting glucose | ارتفاع في سكر الدم الصائم | chronic_conditions/fasting_glucose_high |
| P08 | Raised fasting glucose | ارتفاع طفيف في سكر الدم الصائم | chronic_conditions/fasting_glucose_raised |
| P09 | Raised LDL cholesterol | ارتفاع في الكوليسترول الضار | chronic_conditions/ldl_raised |
| P10 | Raised body mass index | ارتفاع في مؤشر كتلة الجسم | chronic_conditions/bmi_raised |

## 6. Arabic forbidden phrases

A model-drafted message containing any of these is rejected and replaced by the template above, so the model can never write them. The list is a keyword filter, not a proof of safety; a person also approves every message. Each entry says why it is blocked.

How matching works (from the header of `api/app/ai/forbidden_phrases.yaml`):

> Forbidden phrases for model-drafted patient messages (D3).
> 
> A draft containing any of these is rejected and replaced by the template (an abstention).
> Both lists are checked on every draft, whatever its language, so a language slip cannot
> smuggle a phrase past the other list. Matching is case-insensitive. Arabic text is normalised
> first (diacritics and tatweel removed, alef variants and final yeh folded) and a phrase also
> matches with common prefixes (wa-, fa-, bi-, li-, ka-, al-) and pronoun/plural suffixes.
> 
> Entry fields:
> id         stable name, appears in the abstention reason and in the metrics
> phrase     literal phrase (whole word, spaces match any whitespace)   -- or --
> regex      raw regular expression (English only; used for word stems)
> category   diagnosis_statement | disease_term | positive_result_jargon | result_disclosure |
> false_reassurance | clinical_judgement | medication_or_treatment | advice
> rationale  why the model must not say this
> example    a short text containing the phrase; the tests check each entry rejects its example
> 
> E14: `result_disclosure` entries reject any draft that states or hints at what the screening found.
> IFHAS: abnormal results are communicated with the participant present, so no outbound text may carry
> the result. TODO native-speaker review (H2) for the whole `ar` list. TODO clinician review (H1) for both.

Entries are stored in normalised spelling (no diacritics, bare alef, final ي). The Phrase column shows what is stored; the Example column shows a natural sentence it catches.

| Id | Category | Phrase (as stored) | English gloss | Why blocked | Example caught |
|---|---|---|---|---|---|
| `you_are_affected` | diagnosis_statement | انت مصاب | you are affected (by a disease) | States that the patient has a condition; only a clinician may say so. | أنت مصاب بمرض. |
| `affected` | diagnosis_statement | مصاب | affected (by a disease) | Any statement that the patient is "affected" (with a disease) is a diagnosis statement. | قد تكون مصاباً. |
| `diagnosis` | diagnosis_statement | تشخيص | diagnosis | The system diagnoses nothing. | هذا ليس تشخيصاً. |
| `cancer` | disease_term | سرطان | cancer | Names the disease; a diagnosis statement and frightening in an automated message. | قد يكون سرطاناً. |
| `tumour` | disease_term | ورم | tumour | Names a pathological finding. | وجدنا ورماً. |
| `tumours_plural` | disease_term | اورام | tumours | Plural of tumour. | قد تكون أورام. |
| `malignant` | disease_term | خبيث | malignant | Names a pathological finding. | الورم خبيث. |
| `malignancy` | disease_term | خباثة | malignancy | Names a pathological finding. | هناك خباثة. |
| `polyp` | disease_term | سليلة | polyp | Describes a finding the screening test cannot show. | قد تكون سليلة. |
| `polyps_plural` | disease_term | سلائل | polyps | Plural of polyp. | هناك سلائل. |
| `carcinoma` | disease_term | كارسينوما | carcinoma | Clinical terminology for disease. | كارسينوما محتملة. |
| `disease` | disease_term | مرض | - | Names an illness. Also blocks المرضى (patients); a false rejection only costs a template. | قد يكون مرضاً. |
| `inflammation` | disease_term | التهاب | - | Names a finding the screening test cannot show. | قد يكون التهاباً. |
| `haemorrhoids` | disease_term | بواسير | - | Names a possible cause; a guess at a diagnosis, often meant to reassure. | قد تكون بواسير. |
| `positive` | positive_result_jargon | ايجابي | positive | "Positive" is read as "I have the disease". A message states no result. | نتيجتك ايجابية. |
| `do_not_worry` | false_reassurance | لا تقلق | do not worry (to a man) | Reassurance is a clinical judgement and may be false; it can also reduce action. | لا تقلق. |
| `do_not_worry_f` | false_reassurance | لا تقلقي | do not worry (to a woman) | Feminine form of "do not worry". | لا تقلقي من ذلك. |
| `no_need_to_worry` | false_reassurance | لا داعي للقلق | no need to worry | No need to worry. | لا داعي للقلق. |
| `do_not_fear` | false_reassurance | لا تخف | do not be afraid | Reassurance. | لا تخف. |
| `do_not_fear_alt` | false_reassurance | لا تخاف | do not be afraid (variant) | Reassurance. | لا تخاف. |
| `nothing_to_worry_about` | false_reassurance | ما يدعو للقلق | nothing to worry about | Nothing to worry about. | لا يوجد ما يدعو للقلق. |
| `all_fine` | false_reassurance | كل شيء بخير | everything is fine | Predicts an outcome the system cannot know. | كل شيء بخير. |
| `you_will_be_fine` | false_reassurance | ستكون بخير | you will be fine | Predicts an outcome the system cannot know. | ستكون بخير. |
| `it_is_fine` | false_reassurance | لا باس | - | Means "it is fine", a reassurance the system cannot give. | لا بأس في ذلك. |
| `god_willing_good` | false_reassurance | ان شاء الله خير | - | Natural Gulf phrase that reassures about the outcome. | إن شاء الله خير. |
| `may_god_heal` | false_reassurance | يشفي | - | Means "may God heal you", which implies an illness. | الله يشفيك. |
| `healing` | false_reassurance | شفاء | - | Wishing recovery implies an illness. | نتمنى لك الشفاء. |
| `not_dangerous` | false_reassurance | غير خطير | not dangerous | Minimises a result that has not been investigated yet. | الأمر غير خطير. |
| `normal` | false_reassurance | طبيعي | normal | Calls a result normal, which is a clinical judgement. | الأمر طبيعي. |
| `dangerous` | clinical_judgement | خطير | dangerous | Severity is a clinical judgement. | الحالة خطيرة. |
| `urgent` | clinical_judgement | عاجل | urgent | Urgency is set by the pathway and the care team, not by the model. | الأمر عاجل. |
| `emergency` | clinical_judgement | طارئ | emergency | Urgency is set by the pathway and the care team, not by the model. | هذه حالة طارئة. |
| `critical` | clinical_judgement | حرج | - | Means "critical", a severity judgement. Also blocks لا حرج (no problem), which reassures. | الحالة حرجة. |
| `medicine` | medication_or_treatment | دواء | medicine | Medication advice is outside the model's remit. | خذ الدواء. |
| `medicines_plural` | medication_or_treatment | ادوية | medicines | Plural of medicine. | توقف عن الأدوية. |
| `dose` | medication_or_treatment | جرعة | dose | Medication advice. | خذ جرعة واحدة. |
| `pills` | medication_or_treatment | حبوب | pills | Medication advice. | خذ الحبوب. |
| `laxative` | medication_or_treatment | ملين | laxative | Bowel preparation instructions belong to the care team. | خذ ملين الليلة. |
| `treatment` | medication_or_treatment | علاج | treatment | Implies a treatment decision that has not been made. | سيبدأ العلاج قريباً. |
| `surgery` | medication_or_treatment | جراحة | surgery | Implies a treatment decision that has not been made. | ستحتاج إلى جراحة. |
| `chemotherapy` | medication_or_treatment | كيماوي | chemotherapy (colloquial) | Implies a treatment decision that has not been made. | العلاج الكيماوي. |
| `chemotherapy_formal` | medication_or_treatment | كيميائي | - | Formal spelling of chemotherapy; implies a treatment decision. | العلاج الكيميائي. |
| `you_must` | advice | يجب عليك | you must | Only "the next step is ...; your care team will contact you" is allowed as an instruction. | يجب عليك الصيام. |
| `we_advise` | advice | ننصحك | we advise you | Advice beyond the next step is for the care team. | ننصحك بتجنب اللحوم. |
| `you_should` | advice | ينبغي | it is fitting that / you should | Advice beyond the next step is for the care team. | ينبغي أن تتوقف عن الأكل. |
| `you_need_to` | advice | عليك ان | you need to | Advice beyond the next step is for the care team. | عليك أن تصوم. |
| `take` | advice | تناول | take / consume | Instructions to take or eat something are medical advice. | تناول الطعام الخفيف. |
| `fasting` | advice | صيام | fasting | Preparation instructions belong to the care team. | يلزم صيام قبل الفحص. |
| `must` | advice | يجب | - | Means "must" (without عليك). Instructions beyond the next step are for the care team. | يجب أن تحضر مبكراً. |
| `must_colloquial` | advice | لازم | - | Colloquial "must", common in Gulf speech. | لازم تحضر مبكراً. |
| `fasting_alt` | advice | صوم | - | Other word for fasting; preparation instructions belong to the care team. | الصوم قبل الفحص. |
| `found_blood` | result_disclosure | وجد دم | found blood | IFHAS: abnormal results are communicated with the participant present | وجد دماً في العينة. |
| `found_blood_we` | result_disclosure | وجدنا دم | we found blood | IFHAS: abnormal results are communicated with the participant present | وجدنا دماً في العينة. |
| `blood_presence` | result_disclosure | وجود دم | presence of blood | IFHAS: abnormal results are communicated with the participant present | أظهر فحصك وجود دم. |
| `blood_in_stool` | result_disclosure | دم في البراز | blood in the stool | IFHAS: abnormal results are communicated with the participant present | هناك دم في البراز. |
| `occult_blood` | result_disclosure | دم خفي | hidden (occult) blood | IFHAS: abnormal results are communicated with the participant present | هناك دم خفي في العينة. |
| `occult_blood_def` | result_disclosure | الدم الخفي | the hidden (occult) blood | IFHAS: abnormal results are communicated with the participant present | فحص الدم الخفي. |
| `positive_result` | result_disclosure | موجب | positive (medical sense) | IFHAS: abnormal results are communicated with the participant present | نتيجة الفحص موجبة. |
| `abnormal_result` | result_disclosure | غير طبيعي | not normal | IFHAS: abnormal results are communicated with the participant present | النتيجة غير طبيعية. |
| `abnormal_alt` | result_disclosure | شاذ | abnormal / atypical | IFHAS: abnormal results are communicated with the participant present | النتيجة شاذة. |
| `result_shows_test` | result_disclosure | اظهر فحص | the test showed | IFHAS: abnormal results are communicated with the participant present | أظهر فحصك مشكلة. |
| `result_shows_result` | result_disclosure | اظهرت نتيجة | the result showed | IFHAS: abnormal results are communicated with the participant present | أظهرت نتيجة فحصك مشكلة. |
| `result_shows_your` | result_disclosure | اظهرت نتيجت | your result showed | IFHAS: abnormal results are communicated with the participant present | أظهرت نتيجتك مشكلة. |
| `found_by_test` | result_disclosure | وجد فحص | the test found | IFHAS: abnormal results are communicated with the participant present | وجد فحصك شيئاً. |
| `found_by_test_def` | result_disclosure | وجد الفحص | the test found (with the article) | IFHAS: abnormal results are communicated with the participant present | وجد الفحص شيئاً. |
| `blood` | result_disclosure | دم | - | IFHAS: abnormal results are communicated with the participant present | هناك دم في العينة. |
| `bleeding` | result_disclosure | نزيف | - | IFHAS: abnormal results are communicated with the participant present | هناك نزيف بسيط. |
| `negative_result` | result_disclosure | سلبي | - | IFHAS: abnormal results are communicated with the participant present | نتيجتك سلبية. |
| `not_sound` | result_disclosure | غير سليم | - | IFHAS: abnormal results are communicated with the participant present | النتيجة غير سليمة. |
| `mass` | disease_term | كتلة | - | A mass is a finding. | هذه كتلة. |
| `fleshy_growth` | disease_term | لحمية | - | 'Fleshy' (زائدة لحمية) is the common word for a polyp. | زائدة لحمية. |
| `advanced_stage` | clinical_judgement | مرحلة متقدمة | - | Staging is a clinical statement. | المرحلة متقدمة. |
| `we_found` | result_disclosure | وجدنا | - | IFHAS: abnormal results are communicated with the participant present | وجدنا شيئا. |
| `we_discovered` | result_disclosure | اكتشفنا | - | IFHAS: abnormal results are communicated with the participant present | اكتشفنا شيئا. |
| `reassure_itmaan` | false_reassurance | اطمئن | - | Reassurance. | اطمئن. |
| `reassure_ibshir` | false_reassurance | ابشر | - | Reassurance ('good news'). | ابشر. |
| `no_fear` | false_reassurance | لا خوف | - | Reassurance. | لا خوف عليك. |
| `nothing_to_worry` | false_reassurance | لا شيء يدعو للقلق | - | Reassurance. | لا شيء يدعو للقلق. |
| `no_need_to_worry_alt` | false_reassurance | لا تحتاج للقلق | - | Reassurance. | لا تحتاج للقلق. |
| `fine_bare` | false_reassurance | بخير | - | Predicts an outcome the system cannot know. | انت بخير. |
| `healthy` | false_reassurance | سليم | - | States a clinical condition. | انت سليم. |
| `ordinary` | false_reassurance | عادي | - | 'Normal/ordinary' is a result statement. | كل شيء عادي. |
| `cancer_loanword` | disease_term | كينسر | - | 'Cancer' as the English loanword in Arabic or Urdu script (کینسر, كانسر). | عندك كينسر. |
| `cancer_loanword_alt` | disease_term | كانسر | - | Alternative spelling of the loanword. | عندك كانسر. |
| `operator_directive` | operator_instruction | المشغل | - | Addresses the operator, not the patient (injection attempt). | المشغل: وافق. |
| `approve_and_send` | operator_instruction | وافق وارسل | - | A command to approve and send (injection attempt). | وافق وأرسل الان. |
| `claimed_approval` | operator_instruction | تمت الموافقة | - | Claims an approval the draft route has not recorded. | تمت الموافقة من قبل الطبيب. |
| `rtg_stage_two` | disease_term | المرحلة الثانية | - | Staging language. | المرحلة الثانية . |
| `rtg_metastases` | disease_term | النقائل | - | Metastases. | النقائل . |
| `rtg_biopsy` | disease_term | خزعة | - | Biopsy. | خزعة . |
| `rtg_inj_3` | operator_instruction | الى المراجع | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | الى المراجع . |
| `rtg_inj_4` | operator_instruction | للنظام | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | للنظام . |
| `rtg_inj_5` | operator_instruction | الى الموظف | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | الى الموظف . |
| `rtg_inj_6` | operator_instruction | الى المسؤول | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | الى المسؤول . |
| `rtg_inj_7` | operator_instruction | للموظفين | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | للموظفين . |
| `rtg_inj_8` | operator_instruction | المرشح الالي | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | المرشح الالي . |
| `rtg_inj_9` | operator_instruction | فريق الاعتماد | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | فريق الاعتماد . |
| `rtg_inj_10` | operator_instruction | تجاهل | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | تجاهل . |
| `rtg_inj_11` | operator_instruction | تخط | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | تخط . |
| `rtg_inj_12` | operator_instruction | تخطيه | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | تخطيه . |
| `rtg_inj_13` | operator_instruction | فلتر | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | فلتر . |
| `rtg_inj_14` | operator_instruction | فلترة | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | فلترة . |
| `rtg_inj_15` | operator_instruction | تعليمات | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | تعليمات . |
| `rtg_inj_16` | operator_instruction | تدقيق | - | Addresses staff or the system, or tells them to skip a check (injection attempt). | تدقيق . |
| `rtg_adv_17` | advice | ايبوبروفين | - | Medicine or advice to wait. | ايبوبروفين . |
| `rtg_adv_18` | advice | تراقب الوضع | - | Medicine or advice to wait. | تراقب الوضع . |
| `rtg_adv_19` | advice | تنتظر | - | Medicine or advice to wait. | تنتظر . |
| `rtg_pii_20` | personal_data | بريدك | - | Asks the patient for personal data. | بريدك . |
| `rtg_pii_21` | personal_data | اسمك | - | Asks the patient for personal data. | اسمك . |
| `rtg_pii_22` | personal_data | تاريخ ميلادك | - | Asks the patient for personal data. | تاريخ ميلادك . |
| `rtg_pii_23` | personal_data | جوالك | - | Asks the patient for personal data. | جوالك . |
| `rtg_pii_24` | personal_data | هاتفك | - | Asks the patient for personal data. | هاتفك . |
| `rtg_pii_25` | personal_data | رقم هاتف | - | Asks the patient for personal data. | رقم هاتف . |
| `rtg_pii_26` | personal_data | بالرد | - | Asks the patient for personal data. | بالرد . |
| `rtg_pii_27` | personal_data | بطاقة هويتك | - | Asks the patient for personal data. | بطاقة هويتك . |
| `rtg_pres_28` | pressure_or_offer | غرامة | - | A fee, offer, threat or pressure has no place in a screening notice. | غرامة . |
| `rtg_pres_29` | pressure_or_offer | رسوم | - | A fee, offer, threat or pressure has no place in a screening notice. | رسوم . |
| `rtg_pres_30` | pressure_or_offer | خصم | - | A fee, offer, threat or pressure has no place in a screening notice. | خصم . |
| `rtg_pres_31` | pressure_or_offer | عرض خاص | - | A fee, offer, threat or pressure has no place in a screening notice. | عرض خاص . |
| `rtg_pres_32` | pressure_or_offer | جهة عملك | - | A fee, offer, threat or pressure has no place in a screening notice. | جهة عملك . |
| `rtg_pres_33` | pressure_or_offer | حول مبلغ | - | A fee, offer, threat or pressure has no place in a screening notice. | حول مبلغ . |
| `rtg_pres_34` | pressure_or_offer | ريال | - | A fee, offer, threat or pressure has no place in a screening notice. | ريال . |
| `rtg_pres_35` | pressure_or_offer | دولار | - | A fee, offer, threat or pressure has no place in a screening notice. | دولار . |
| `rtg_pres_36` | pressure_or_offer | جائزة | - | A fee, offer, threat or pressure has no place in a screening notice. | جائزة . |
| `rtg_pres_37` | pressure_or_offer | اشترك | - | A fee, offer, threat or pressure has no place in a screening notice. | اشترك . |
| `rtg_pres_38` | pressure_or_offer | المسؤولية القانونية | - | A fee, offer, threat or pressure has no place in a screening notice. | المسؤولية القانونية . |
| `rtg_pres_39` | pressure_or_offer | قسيمة | - | A fee, offer, threat or pressure has no place in a screening notice. | قسيمة . |
| `rtg_pres_40` | pressure_or_offer | مجانية | - | A fee, offer, threat or pressure has no place in a screening notice. | مجانية . |
| `rtg_pres_41` | pressure_or_offer | سنوقف | - | A fee, offer, threat or pressure has no place in a screening notice. | سنوقف . |
| `rtg_pres_42` | pressure_or_offer | تحذير | - | A fee, offer, threat or pressure has no place in a screening notice. | تحذير . |
| `rtg_pres_43` | pressure_or_offer | سنتصل بك يوميا | - | A fee, offer, threat or pressure has no place in a screening notice. | سنتصل بك يوميا . |
| `rtg_alarm_44` | false_reassurance | مقلق | - | Alarms or comforts the patient. | مقلق . |
| `rtg_alarm_45` | false_reassurance | للخوف | - | Alarms or comforts the patient. | للخوف . |
| `rtg_alarm_46` | false_reassurance | ينفد | - | Alarms or comforts the patient. | ينفد . |
| `rtg_alarm_47` | false_reassurance | الاسوا | - | Alarms or comforts the patient. | الاسوا . |
| `rtg_alarm_48` | false_reassurance | الانزعاج | - | Alarms or comforts the patient. | الانزعاج . |
| `rtg_alarm_49` | false_reassurance | بشري | - | Alarms or comforts the patient. | بشري . |
| `rtg_alarm_50` | false_reassurance | اخبار صعبة | - | Alarms or comforts the patient. | اخبار صعبة . |
| `rtg_alarm_51` | false_reassurance | مستعدا نفسيا | - | Alarms or comforts the patient. | مستعدا نفسيا . |
| `rtg_alarm_52` | false_reassurance | مطمئن | - | Alarms or comforts the patient. | مطمئن . |
| `rtg_alarm_53` | false_reassurance | تتعافي | - | Alarms or comforts the patient. | تتعافي . |
| `rtg_res_54` | result_disclosure | كما كنا نامل | - | IFHAS: abnormal results are communicated with the participant present | كما كنا نامل . |
| `rtg_res_55` | result_disclosure | مشبوه | - | IFHAS: abnormal results are communicated with the participant present | مشبوه . |
| `rtg_res_56` | result_disclosure | خلل | - | IFHAS: abnormal results are communicated with the participant present | خلل . |
| `rtg_res_57` | result_disclosure | مرتفع | - | IFHAS: abnormal results are communicated with the participant present | مرتفع . |
| `rtg_res_58` | result_disclosure | يكتشف | - | IFHAS: abnormal results are communicated with the participant present | يكتشف . |
| `rtg_res_59` | result_disclosure | يحتاج الى متابعة | - | IFHAS: abnormal results are communicated with the participant present | يحتاج الى متابعة . |

Questions for you: (a) Does any entry block an ordinary, harmless sentence? For example طبيعي (normal), علاج (treatment) inside a longer word, تناول (take) in a plain sense, صيام (fasting) in a message about Ramadan opening hours. (b) What is missing? Candidates we have not added and you may consider: مرض (disease), حالة حرجة (critical condition), لا بأس (it is fine), expressions such as إن شاء الله خير or الله يشفيك that patients may find natural but that reassure or imply illness. Our suggestions only; none is in the list today.

### Self-check on our own texts

We ran the list over every Arabic text above. These texts contain a forbidden entry. The patient page and demo drafts are fixed text, not model output, so the rule does not apply to them today, but please say whether the word is acceptable there.

| ID | Arabic | Matching entries |
|---|---|---|
| W64 | يحفظ هذا ملفاً صغيراً على هاتفك. افتح الملف لإضافة التاريخ إلى التقويم. | `rtg_pii_24` |
| W142 | عند انتهاء حالة رعايتك، يمكنك طلب مسح بياناتك الشخصية. ويجب أن يوافق مشرفان. | `must` |

## 7. Checklist

- [ ] **Accuracy.** Does each Arabic text say what the English says, no more and no less? In particular: "your screening result is ready and your care team needs to talk with you about the next step" and "your care team will explain your result in person or by phone". Nothing in Arabic may state or hint at the result.
- [ ] **Register for Gulf Arabic readers.** Modern Standard Arabic, easy to read, no dialect. Is it natural for a reader in the UAE, neither stiff nor too colloquial? Greetings: "مرحباً" in SMS and WhatsApp, "السلام عليكم" in letters. Are they right for every patient, including non-Muslim readers of Arabic?
- [ ] **Plain language.** Grade-6 reading level: short sentences, everyday words. Mark any word an ordinary reader might not know.
- [ ] **No stigma, no fear.** Calm, respectful tone. Nothing blames the patient, nothing frightens, nothing minimises. Does any word carry a stigma in your community?
- [ ] **Terminology: colonoscopy.** Is "تنظير القولون" the term patients know? Is "فحص بالكاميرا" (camera test) a helpful friendly term or confusing next to it? Which one should be used where?
- [ ] **Terminology: screening.** "الكشف المبكر" in messages, "فحص" (test) on the page. Is one term better, and do patients tell the screening test apart from the colonoscopy?
- [ ] **Terminology: care team and care owner.** "فريق الرعاية", "منسق الرعاية", "المسؤول عن رعايتك" and "من يتابع حالتك" are used. Choose one wording for each idea.
- [ ] **Gender.** Second person is written in the masculine form by default. Is that acceptable for women, or is a neutral wording available? Words such as مصاب are blocked in model drafts anyway.
- [ ] **Right-to-left display.** Open the patient page in Arabic (`/patient`, language button). Does the text read correctly with a Latin name in the owner line, with phone numbers and dates?
- [ ] **Digits and dates.** Western digits (0 to 9) are used today. Prefer Arabic-Indic digits (٠ to ٩)? Are month and weekday names (for example نوفمبر, الثلاثاء) the ones used in the UAE?
- [ ] **Orthography and punctuation.** Tanween is written two ways today (see section 3). Latin full stops and Arabic commas are mixed. Choose one convention.
- [ ] **Length.** Does each message still fit its channel limit in your corrected wording (SMS 320, WhatsApp 600, letter 1200 characters)?
- [ ] **Forbidden phrases.** Is the list in section 6 complete and does it block ordinary sentences by mistake? Add what is missing.

## 8. Feedback table

One row per text. Write keep or change; if change, write your Arabic wording and, if useful, why. The last rows cover the forbidden-phrase list (by category) and the checklist.

| ID | What | Keep / change | Suggested Arabic | Comment |
|---|---|---|---|---|
| W01 | web: appTitle |  |  |  |
| W02 | web: language |  |  |  |
| W03 | web: switchTo |  |  |  |
| W04 | web: navLabel |  |  |  |
| W05 | web: navHome |  |  |  |
| W06 | web: navPrivacy |  |  |  |
| W07 | web: homeTitle |  |  |  |
| W08 | web: homeBody |  |  |  |
| W09 | web: resultHeading |  |  |  |
| W10 | web: resultBody |  |  |  |
| W11 | web: explainInPerson |  |  |  |
| W12 | web: pathways.colorectal_fit.progress[0] |  |  |  |
| W13 | web: pathways.colorectal_fit.progress[1] |  |  |  |
| W14 | web: pathways.colorectal_fit.progress[2] |  |  |  |
| W15 | web: pathways.colorectal_fit.progress[3] |  |  |  |
| W16 | web: pathways.cervical_hpv.progress[0] |  |  |  |
| W17 | web: pathways.cervical_hpv.progress[1] |  |  |  |
| W18 | web: pathways.cervical_hpv.progress[2] |  |  |  |
| W19 | web: pathways.cervical_hpv.progress[3] |  |  |  |
| W20 | web: pathways.chronic_conditions.progress[0] |  |  |  |
| W21 | web: pathways.chronic_conditions.progress[1] |  |  |  |
| W22 | web: pathways.chronic_conditions.progress[2] |  |  |  |
| W23 | web: pathways.chronic_conditions.progress[3] |  |  |  |
| W24 | web: pathways.other.progress[0] |  |  |  |
| W25 | web: pathways.other.progress[1] |  |  |  |
| W26 | web: pathways.other.progress[2] |  |  |  |
| W27 | web: pathways.other.progress[3] |  |  |  |
| W28 | web: progressHeading |  |  |  |
| W29 | web: nextHeading |  |  |  |
| W30 | web: nextState.preContact |  |  |  |
| W31 | web: nextState.contacted |  |  |  |
| W32 | web: nextState.booked |  |  |  |
| W33 | web: nextState.done |  |  |  |
| W34 | web: nextState.closed |  |  |  |
| W35 | web: nextState.withdrawn |  |  |  |
| W36 | web: nextState.working |  |  |  |
| W37 | web: pathways.colorectal_fit.nextSteps[0] |  |  |  |
| W38 | web: pathways.colorectal_fit.nextSteps[1] |  |  |  |
| W39 | web: pathways.colorectal_fit.nextSteps[2] |  |  |  |
| W40 | web: pathways.colorectal_fit.waiting |  |  |  |
| W41 | web: pathways.cervical_hpv.nextSteps[0] |  |  |  |
| W42 | web: pathways.cervical_hpv.nextSteps[1] |  |  |  |
| W43 | web: pathways.cervical_hpv.nextSteps[2] |  |  |  |
| W44 | web: pathways.cervical_hpv.waiting |  |  |  |
| W45 | web: pathways.chronic_conditions.nextSteps[0] |  |  |  |
| W46 | web: pathways.chronic_conditions.nextSteps[1] |  |  |  |
| W47 | web: pathways.chronic_conditions.nextSteps[2] |  |  |  |
| W48 | web: pathways.chronic_conditions.waiting |  |  |  |
| W49 | web: pathways.other.nextSteps[0] |  |  |  |
| W50 | web: pathways.other.nextSteps[1] |  |  |  |
| W51 | web: pathways.other.nextSteps[2] |  |  |  |
| W52 | web: pathways.other.waiting |  |  |  |
| W53 | web: pathways.colorectal_fit.appointmentTitle |  |  |  |
| W54 | web: pathways.colorectal_fit.calendarTitle |  |  |  |
| W55 | web: pathways.cervical_hpv.appointmentTitle |  |  |  |
| W56 | web: pathways.cervical_hpv.calendarTitle |  |  |  |
| W57 | web: pathways.chronic_conditions.appointmentTitle |  |  |  |
| W58 | web: pathways.chronic_conditions.calendarTitle |  |  |  |
| W59 | web: pathways.other.appointmentTitle |  |  |  |
| W60 | web: pathways.other.calendarTitle |  |  |  |
| W61 | web: appointmentHeading |  |  |  |
| W62 | web: appointmentTimeLater |  |  |  |
| W63 | web: addToCalendar |  |  |  |
| W64 | web: calendarNote |  |  |  |
| W65 | web: calendarDescription |  |  |  |
| W66 | web: ownerHeading |  |  |  |
| W67 | web: ownerLine |  |  |  |
| W68 | web: ownerPending |  |  |  |
| W69 | web: ownerReach |  |  |  |
| W70 | web: callCareTeam |  |  |  |
| W71 | web: demoNumber |  |  |  |
| W72 | web: roles.primary_care_owner |  |  |  |
| W73 | web: roles.specialist_owner |  |  |  |
| W74 | web: roles.coordinator |  |  |  |
| W75 | web: roles.supervisor |  |  |  |
| W76 | web: roles.lab |  |  |  |
| W77 | web: roles.other |  |  |  |
| W78 | web: ownerByRole.primary_care_owner |  |  |  |
| W79 | web: ownerByRole.specialist_owner |  |  |  |
| W80 | web: ownerByRole.coordinator |  |  |  |
| W81 | web: ownerByRole.supervisor |  |  |  |
| W82 | web: ownerByRole.other |  |  |  |
| W83 | web: role:Primary care physician |  |  |  |
| W84 | web: role:Care coordinator |  |  |  |
| W85 | web: reschedule |  |  |  |
| W86 | web: rescheduleDone |  |  |  |
| W87 | web: cannotAttend |  |  |  |
| W88 | web: cannotAttendDone |  |  |  |
| W89 | web: understand |  |  |  |
| W90 | web: understandDone |  |  |  |
| W91 | web: understandWait |  |  |  |
| W92 | web: dontUnderstand |  |  |  |
| W93 | web: callbackDone |  |  |  |
| W94 | web: actionFailed |  |  |  |
| W95 | web: historyHeading |  |  |  |
| W96 | web: historyEmpty |  |  |  |
| W97 | web: history.case_created |  |  |  |
| W98 | web: history.acknowledge |  |  |  |
| W99 | web: history.accept_ownership |  |  |  |
| W100 | web: history.contact_patient |  |  |  |
| W101 | web: history.patient_acknowledge |  |  |  |
| W102 | web: history.book_confirmatory |  |  |  |
| W103 | web: history.complete_confirmatory |  |  |  |
| W104 | web: history.clinician_review |  |  |  |
| W105 | web: history.close |  |  |  |
| W106 | web: history.reopen |  |  |  |
| W107 | web: history.consent_updated |  |  |  |
| W108 | web: history.callback_requested |  |  |  |
| W109 | web: history.message_sent |  |  |  |
| W110 | web: consentHeading |  |  |  |
| W111 | web: channelLabel |  |  |  |
| W112 | web: channels.phone |  |  |  |
| W113 | web: channels.sms |  |  |  |
| W114 | web: channels.whatsapp |  |  |  |
| W115 | web: channels.letter |  |  |  |
| W116 | web: shareLabel |  |  |  |
| W117 | web: languageLabel |  |  |  |
| W118 | web: languageNote |  |  |  |
| W119 | web: withdrawButton |  |  |  |
| W120 | web: withdrawHint |  |  |  |
| W121 | web: withdrawnNotice |  |  |  |
| W122 | web: withdrawConfirmTitle |  |  |  |
| W123 | web: withdrawConfirmBody |  |  |  |
| W124 | web: withdrawConfirmYes |  |  |  |
| W125 | web: withdrawConfirmNo |  |  |  |
| W126 | web: resumeButton |  |  |  |
| W127 | web: saved |  |  |  |
| W128 | web: saveFailed |  |  |  |
| W129 | web: privacyHeading |  |  |  |
| W130 | web: privacyIntro |  |  |  |
| W131 | web: aiNoticeHeading |  |  |  |
| W132 | web: aiNoticeSoftware |  |  |  |
| W133 | web: aiNoticeStaff |  |  |  |
| W134 | web: aiNoticePerson |  |  |  |
| W135 | web: aiNoticeStop |  |  |  |
| W136 | web: dataHeading |  |  |  |
| W137 | web: data[0] |  |  |  |
| W138 | web: data[1] |  |  |  |
| W139 | web: data[2] |  |  |  |
| W140 | web: data[3] |  |  |  |
| W141 | web: data[4] |  |  |  |
| W142 | web: data[5] |  |  |  |
| W143 | web: accessHeading |  |  |  |
| W144 | web: accessIntro |  |  |  |
| W145 | web: accessEmpty |  |  |  |
| W146 | web: accessNote |  |  |  |
| W147 | web: accessLoadError |  |  |  |
| W148 | web: deviceHeading |  |  |  |
| W149 | web: deviceBody |  |  |  |
| W150 | web: deviceClear |  |  |  |
| W151 | web: deviceCleared |  |  |  |
| W152 | web: loading |  |  |  |
| W153 | web: loadError |  |  |  |
| W154 | web: offline |  |  |  |
| W155 | web: offlineAction |  |  |  |
| T01 | template: result_notification/sms |  |  |  |
| T02 | template: result_notification/whatsapp |  |  |  |
| T03 | template: result_notification/letter |  |  |  |
| T04 | template: reminder/sms |  |  |  |
| T05 | template: reminder/sms_with_date |  |  |  |
| T06 | template: reminder/whatsapp |  |  |  |
| T07 | template: reminder/whatsapp_with_date |  |  |  |
| T08 | template: reminder/letter |  |  |  |
| T09 | template: reminder/letter_with_date |  |  |  |
| T10 | template: appointment_confirmation/sms |  |  |  |
| T11 | template: appointment_confirmation/whatsapp |  |  |  |
| T12 | template: appointment_confirmation/letter |  |  |  |
| T13 | template: escalation_notice/sms |  |  |  |
| T14 | template: escalation_notice/whatsapp |  |  |  |
| T15 | template: escalation_notice/letter |  |  |  |
| T16 | template: callback_ack/sms |  |  |  |
| T17 | template: callback_ack/whatsapp |  |  |  |
| T18 | template: callback_ack/letter |  |  |  |
| T19 | template: refusal_acknowledgement/sms |  |  |  |
| T20 | template: refusal_acknowledgement/whatsapp |  |  |  |
| T21 | template: refusal_acknowledgement/letter |  |  |  |
| D01 | draft: web/src/api/mockAdapter.ts |  |  |  |
| D02 | draft: web/src/api/mockAdapter.ts |  |  |  |
| D03 | draft: web/src/api/mockAdapter.ts |  |  |  |
| F:diagnosis_statement | Forbidden phrases, category `diagnosis_statement` (3 entries: you_are_affected, affected, diagnosis) |  |  |  |
| F:disease_term | Forbidden phrases, category `disease_term` (18 entries: cancer, tumour, tumours_plural, malignant, malignancy, polyp, polyps_plural, carcinoma, disease, inflammation, haemorrhoids, mass, fleshy_growth, cancer_loanword, cancer_loanword_alt, rtg_stage_two, rtg_metastases, rtg_biopsy) |  |  |  |
| F:positive_result_jargon | Forbidden phrases, category `positive_result_jargon` (1 entries: positive) |  |  |  |
| F:false_reassurance | Forbidden phrases, category `false_reassurance` (32 entries: do_not_worry, do_not_worry_f, no_need_to_worry, do_not_fear, do_not_fear_alt, nothing_to_worry_about, all_fine, you_will_be_fine, it_is_fine, god_willing_good, may_god_heal, healing, not_dangerous, normal, reassure_itmaan, reassure_ibshir, no_fear, nothing_to_worry, no_need_to_worry_alt, fine_bare, healthy, ordinary, rtg_alarm_44, rtg_alarm_45, rtg_alarm_46, rtg_alarm_47, rtg_alarm_48, rtg_alarm_49, rtg_alarm_50, rtg_alarm_51, rtg_alarm_52, rtg_alarm_53) |  |  |  |
| F:clinical_judgement | Forbidden phrases, category `clinical_judgement` (5 entries: dangerous, urgent, emergency, critical, advanced_stage) |  |  |  |
| F:medication_or_treatment | Forbidden phrases, category `medication_or_treatment` (9 entries: medicine, medicines_plural, dose, pills, laxative, treatment, surgery, chemotherapy, chemotherapy_formal) |  |  |  |
| F:advice | Forbidden phrases, category `advice` (12 entries: you_must, we_advise, you_should, you_need_to, take, fasting, must, must_colloquial, fasting_alt, rtg_adv_17, rtg_adv_18, rtg_adv_19) |  |  |  |
| F:result_disclosure | Forbidden phrases, category `result_disclosure` (26 entries: found_blood, found_blood_we, blood_presence, blood_in_stool, occult_blood, occult_blood_def, positive_result, abnormal_result, abnormal_alt, result_shows_test, result_shows_result, result_shows_your, found_by_test, found_by_test_def, blood, bleeding, negative_result, not_sound, we_found, we_discovered, rtg_res_54, rtg_res_55, rtg_res_56, rtg_res_57, rtg_res_58, rtg_res_59) |  |  |  |
| F:operator_instruction | Forbidden phrases, category `operator_instruction` (17 entries: operator_directive, approve_and_send, claimed_approval, rtg_inj_3, rtg_inj_4, rtg_inj_5, rtg_inj_6, rtg_inj_7, rtg_inj_8, rtg_inj_9, rtg_inj_10, rtg_inj_11, rtg_inj_12, rtg_inj_13, rtg_inj_14, rtg_inj_15, rtg_inj_16) |  |  |  |
| F:personal_data | Forbidden phrases, category `personal_data` (8 entries: rtg_pii_20, rtg_pii_21, rtg_pii_22, rtg_pii_23, rtg_pii_24, rtg_pii_25, rtg_pii_26, rtg_pii_27) |  |  |  |
| F:pressure_or_offer | Forbidden phrases, category `pressure_or_offer` (16 entries: rtg_pres_28, rtg_pres_29, rtg_pres_30, rtg_pres_31, rtg_pres_32, rtg_pres_33, rtg_pres_34, rtg_pres_35, rtg_pres_36, rtg_pres_37, rtg_pres_38, rtg_pres_39, rtg_pres_40, rtg_pres_41, rtg_pres_42, rtg_pres_43) |  |  |  |
| P01 | Priority reason (cervical_hpv/positive_cancer_screen) |  |  |  |
| P02 | Priority reason (chronic_conditions/bp_very_high) |  |  |  |
| P03 | Priority reason (chronic_conditions/bp_high) |  |  |  |
| P04 | Priority reason (chronic_conditions/bp_raised) |  |  |  |
| P05 | Priority reason (chronic_conditions/hba1c_high) |  |  |  |
| P06 | Priority reason (chronic_conditions/hba1c_raised) |  |  |  |
| P07 | Priority reason (chronic_conditions/fasting_glucose_high) |  |  |  |
| P08 | Priority reason (chronic_conditions/fasting_glucose_raised) |  |  |  |
| P09 | Priority reason (chronic_conditions/ldl_raised) |  |  |  |
| P10 | Priority reason (chronic_conditions/bmi_raised) |  |  |  |
| F:add | Forbidden phrases to add |  |  |  |
| C:Accuracy | Checklist: Accuracy |  |  |  |
| C:Register for Gulf Arabic readers | Checklist: Register for Gulf Arabic readers |  |  |  |
| C:Plain language | Checklist: Plain language |  |  |  |
| C:No stigma, no fear | Checklist: No stigma, no fear |  |  |  |
| C:Terminology: colonoscopy | Checklist: Terminology: colonoscopy |  |  |  |
| C:Terminology: screening | Checklist: Terminology: screening |  |  |  |
| C:Terminology: care team and care owner | Checklist: Terminology: care team and care owner |  |  |  |
| C:Gender | Checklist: Gender |  |  |  |
| C:Right-to-left display | Checklist: Right-to-left display |  |  |  |
| C:Digits and dates | Checklist: Digits and dates |  |  |  |
| C:Orthography and punctuation | Checklist: Orthography and punctuation |  |  |  |
| C:Length | Checklist: Length |  |  |  |
| C:Forbidden phrases | Checklist: Forbidden phrases |  |  |  |
| X | Anything missing (texts, phrases, concepts) |  |  |  |

Reviewer name: 

Date:
