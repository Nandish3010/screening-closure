"""Templates and slot pools for the synthetic patient-reply generator (D13).

Everything here is SYNTHETIC and written by hand for this project. It is not real patient language: it is
what a developer imagines a patient might type. See docs/governance/reply-triage-card.md, "Data and its limits".

Layout:  TEMPLATES[intent][variant] = [template, ...]
A template is a `str.format` string. A slot `{day}` is drawn from the pool of the template's own variant; a slot
with a suffix (`{day_en}`, `{day_ar}`, `{day_ax}`) is drawn from the English, Arabic (Gulf) or Arabizi pool, which is
how the `mixed` templates combine scripts.

Variants: en (English), ar_msa (Modern Standard Arabic), ar_gulf (Gulf colloquial Arabic, Emirati and neighbours),
arabizi (Arabic written in Latin letters and digits), mixed (Arabic and Latin script in one message).

No template contains a name, a phone number or any identifier.
"""

from __future__ import annotations

INTENTS = (
    "confirm",
    "reschedule",
    "cannot_attend",
    "wrong_number",
    "dont_understand",
    "opt_out",
    "question",
    "already_done",
    "other",
)
VARIANTS = ("en", "ar_msa", "ar_gulf", "arabizi", "mixed")

# Slot pools per variant. Entries may be empty strings (the slot then vanishes).
POOLS: dict[str, dict[str, list[str]]] = {
    "en": {
        "greet": ["", "", "Hello", "Hi", "Hello,", "Good morning", "Salam", "Hi team", "Hello doctor", "Assalamu alaikum"],
        "close": ["", "", "Thanks", "Thank you", "Thanks a lot", "thx", "Regards", "Thank you very much"],
        "day": ["tomorrow", "on Sunday", "on Monday", "next week", "this Thursday", "on Wednesday",
                "the day after tomorrow", "on the 25th", "next month", "after the weekend"],
        "day2": ["Tuesday", "next Sunday", "the end of the month", "Saturday", "in two weeks"],
        "time": ["10 am", "in the morning", "after 4 pm", "after work", "at 2:30", "in the evening", "before noon"],
        "place": ["the clinic", "the hospital", "the centre", "your clinic"],
        "appt": ["appointment", "visit", "test", "check-up", "call"],
        "person": ["my wife", "my husband", "my father", "my mother", "my brother"],
        "reason": ["work", "travel", "a family emergency", "my exams", "being abroad", "my duty shift", "being unwell"],
        "reason2": ["abroad", "travelling", "out of the city", "on leave overseas", "at work all week"],
        "ok": ["OK", "Okay", "Sure", "Yes", "Noted", "Confirmed", "Alright", "Fine", "ok"],
        "when": ["last week", "last month", "two weeks ago", "last year", "yesterday", "a few days ago"],
        "other": ["God bless you", "Ramadan Kareem", "Eid Mubarak", "Happy new year", "Have a nice day"],
    },
    "ar_msa": {
        "greet": ["", "", "السلام عليكم", "مرحبا", "مرحبًا", "صباح الخير", "مساء الخير"],
        "close": ["", "", "شكرا لكم", "شكرًا جزيلا", "مع الشكر", "وشكرا", "جزاكم الله خيرا"],
        "day": ["غدا", "يوم الأحد", "يوم الاثنين", "الأسبوع القادم", "يوم الخميس", "يوم الأربعاء", "بعد غد",
                "الشهر القادم", "بعد نهاية الأسبوع"],
        "day2": ["يوم الثلاثاء", "الأحد القادم", "نهاية الشهر", "يوم السبت", "بعد أسبوعين"],
        "time": ["الساعة العاشرة صباحا", "في الصباح", "بعد الساعة الرابعة", "بعد الدوام", "في المساء", "قبل الظهر",
                 "الساعة ١٠ صباحا"],
        "place": ["العيادة", "المستشفى", "المركز"],
        "appt": ["الموعد", "الزيارة", "الفحص", "المكالمة"],
        "person": ["زوجتي", "زوجي", "والدي", "والدتي", "أخي"],
        "reason": ["ظروف العمل", "السفر", "ظرف عائلي طارئ", "الامتحانات", "المرض", "نوبة العمل"],
        "reason2": ["خارج البلاد", "مسافر", "خارج المدينة", "في إجازة", "على رأس العمل طوال الأسبوع"],
        "ok": ["حسنا", "تمام", "موافق", "تم", "علم", "حاضر"],
        "when": ["الأسبوع الماضي", "الشهر الماضي", "قبل أسبوعين", "العام الماضي", "أمس", "قبل أيام"],
        "other": ["رمضان كريم", "عيد مبارك", "كل عام وأنتم بخير", "بارك الله فيكم", "أتمنى لكم يوما سعيدا"],
    },
    "ar_gulf": {
        "greet": ["", "", "هلا", "مرحبا", "السلام عليكم", "هلا والله", "صباح الخير", "يا هلا"],
        "close": ["", "", "يعطيك العافية", "تسلم", "مشكورين", "الله يعافيك", "شكرا", "الله يسلمك"],
        "day": ["بكرة", "يوم الأحد", "يوم الاثنين", "الأسبوع الجاي", "يوم الخميس", "بعد بكرة", "الشهر الجاي",
                "عقب صلاة الجمعة", "عقب الويكند"],
        "day2": ["يوم الثلاثاء", "الأحد الجاي", "آخر الشهر", "يوم السبت", "عقب أسبوعين"],
        "time": ["العاشرة الصبح", "الصبح", "بعد الساعة ٤", "عقب الدوام", "العصر", "بالليل", "قبل الظهر"],
        "place": ["العيادة", "المستشفى", "المركز"],
        "appt": ["الموعد", "الفحص", "المكالمة", "الزيارة"],
        "person": ["الحرمة", "ريلي", "الوالد", "الوالدة", "خوي"],
        "reason": ["الشغل", "السفر", "ظرف طارئ", "الاختبارات", "تعبان شوي", "الدوام"],
        "reason2": ["برا البلاد", "مسافر", "برا المدينة", "في إجازة", "مداوم طول الأسبوع"],
        "ok": ["تمام", "زين", "أوكي", "ان شاء الله", "أكيد", "حاضر", "ماشي"],
        "when": ["الأسبوع اللي فات", "الشهر اللي فات", "من أسبوعين", "السنة اللي فاتت", "أمس", "من كم يوم"],
        "other": ["الله يحفظك", "رمضان كريم", "عيد مبارك", "كل عام وانتم بخير", "الله يوفقكم"],
    },
    "arabizi": {
        "greet": ["", "", "salam", "marhaba", "hala", "salam alaikum", "sabah el kheir", "hala wallah"],
        "close": ["", "", "shukran", "yaatik el afya", "thanks", "mashkoor", "tislam", "thank u"],
        "day": ["bukra", "yom el ahad", "yom el ithnain", "el usbou3 el jay", "yom el khamis", "ba3d bukra",
                "el shahr el jay", "yom el arb3a", "ba3d el weekend"],
        "day2": ["yom el thalatha", "el ahad el jay", "akhir el shahr", "yom el sabt", "ba3d usbou3ain"],
        "time": ["el 3ashira el subh", "el subh", "ba3d el saa3a 4", "ba3d el dawam", "el 3asr", "bel layl", "9abl el dhuhr"],
        "place": ["el 3iyada", "el mustashfa", "el markaz"],
        "appt": ["el maw3id", "el fahs", "el mukalama", "el ziyara"],
        "person": ["el hurma", "zawji", "el walid", "el walida", "akhooy"],
        "reason": ["el shughul", "el safar", "zarf tare2", "el imtihanat", "ta3ban shway", "el dawam"],
        "reason2": ["bara el balad", "msafir", "bara el madina", "fi ijaza", "mdawem tool el usbou3"],
        "ok": ["tamam", "zain", "okay", "inshallah", "akeed", "haader", "ok", "mashi"],
        "when": ["el usbou3 el madi", "el shahr el madi", "men usbou3ain", "el sana el madya", "embarih", "men kam yom"],
        "other": ["allah yihfazak", "ramadan kareem", "eid mubarak", "kul 3am wintum bkheir", "allah yewafe9kum"],
    },
}
# the `mixed` variant draws its slots from these pools (suffix _en, _ar, _ax)
SUFFIX_POOL = {"en": "en", "ar": "ar_gulf", "ax": "arabizi"}

T: dict[str, dict[str, list[str]]] = {}

# --------------------------------------------------------------------------------------------- confirm
T["confirm"] = {
    "en": [
        "{greet} {ok}, I will be available {day}. {close}",
        "{ok}, I got the message. {close}",
        "{greet} yes, please call me {time}. {close}",
        "{ok}, I will be there {day} {time}",
        "{greet} I confirm my {appt} {day}. {close}",
        "Yes I am available {day}, you can call me",
        "{ok} I understand. Please go ahead and call",
        "Received, thank you. I will wait for the call {time}",
        "I will come to {place} {day} {time}. {close}",
        "{ok} noted",
    ],
    "ar_msa": [
        "{greet} {ok}، سأكون متاحا {day}. {close}",
        "{ok}، استلمت الرسالة. {close}",
        "{greet} نعم، يمكنكم الاتصال بي {time}. {close}",
        "أؤكد حضوري {day}. {close}",
        "{ok}، فهمت. تفضلوا بالاتصال بي",
        "سأحضر إلى {place} {day} {time}",
        "تم الاستلام، شكرا. سأنتظر اتصالكم {time}",
        "{ok} {close}",
    ],
    "ar_gulf": [
        "{greet} {ok}، أنا متفرغ {day}. {close}",
        "{ok}، وصلتني الرسالة {close}",
        "{greet} إي اتصلوا فيني {time}. {close}",
        "{ok} بجي {place} {day} {time}",
        "{greet} أأكد الموعد {day}، {close}",
        "وصلت الرسالة، بنتظر اتصالكم {time}",
        "{ok} فهمت، اتصلوا فيني",
        "{ok}",
    ],
    "arabizi": [
        "{greet} {ok}, ana mitfarrigh {day}. {close}",
        "{ok}, wasalat el resala {close}",
        "{greet} aywa ittasilo feeni {time}. {close}",
        "{ok} bajee {place} {day} {time}",
        "{greet} ba2akid {appt} {day}, {close}",
        "wasalat el resala, bantidhir ittisalkum {time}",
        "{ok} fhemt, ittasilo fiyya",
        "{ok}",
    ],
    "mixed": [
        "{ok_ar} I will come {day_en} {close_en}",
        "Confirmed، {ok_ar} {day_ar}",
        "{greet_ar} yes I am available {day_en}، تقدرون تتصلون",
        "تمام {ok_en}, call me {time_en}",
        "{ok_en} شكرا، وصلت الرسالة",
        "okay بجي {day_ar} {time_ar}",
    ],
}

# ------------------------------------------------------------------------------------------ reschedule
T["reschedule"] = {
    "en": [
        "{greet} can we move the {appt} to {day}? {close}",
        "Is it possible to change the time to {time}? {close}",
        "I can't do {day}, can you call me {day2} instead",
        "Please reschedule my {appt} for {day}",
        "{greet} I would like another date for the {appt}, {day} would work",
        "Can I come {day2} instead of {day}?",
        "Could we do it later, maybe {day}? {close}",
        "Please call me {time} not now, I am busy",
        "Need to postpone the {appt} to {day}. {close}",
        "{greet} {day} does not suit me, any slot {day2}?",
    ],
    "ar_msa": [
        "{greet} هل يمكن تغيير {appt} إلى {day}؟ {close}",
        "أرجو تأجيل {appt} إلى {day}",
        "لا أستطيع {day}، هل يمكن الاتصال بي {day2}؟",
        "هل من الممكن تعديل الوقت ليكون {time}؟",
        "أحتاج موعدا آخر، يناسبني {day}. {close}",
        "أرجو الاتصال بي {time} لأنني مشغول الآن",
        "هل يمكن تأجيل {appt} قليلا؟ {day} مناسب لي",
        "{greet} {day} لا يناسبني، هل يتوفر موعد {day2}؟",
    ],
    "ar_gulf": [
        "{greet} ممكن نغير {appt} ل{day}؟ {close}",
        "ابغى أأجل {appt} إلى {day}",
        "ما اقدر {day}، ممكن تتصلون فيني {day2}؟",
        "ممكن نغير الوقت يكون {time}؟",
        "أبي موعد ثاني، {day} يناسبني. {close}",
        "اتصلوا فيني {time}، الحين مشغول",
        "{greet} {day} مب مناسب لي، في موعد {day2}؟",
        "ممكن تأجلون {appt} شوي؟ {day} أحسن",
    ],
    "arabizi": [
        "{greet} momken nghayyer {appt} {day}? {close}",
        "abgha a2ajjel {appt} la {day}",
        "ma a2dar {day}, momken titasilo feeni {day2}?",
        "momken nghayyer el waqt yekoon {time}?",
        "abi maw3id thani, {day} yenasibni. {close}",
        "ittasilo feeni {time}, el7een mashghool",
        "{greet} {day} mub munasib, fi maw3id {day2}?",
        "momken te2ajjilon {appt} shway? {day} a7san",
    ],
    "mixed": [
        "ممكن reschedule {appt_ar} ل{day_ar}؟",
        "Can we change the time ل{time_ar}? {close_en}",
        "sorry ما اقدر {day_ar}, call me {day2_en} please",
        "{greet_ar} I need another date، {day_ar} يناسبني",
        "please اتصلوا فيني {time_ar}, now I am busy",
        "postpone {appt_en} to {day_en} لو سمحتم",
    ],
}

# --------------------------------------------------------------------------------------- cannot_attend
T["cannot_attend"] = {
    "en": [
        "{greet} sorry I can't make it, {reason}. {close}",
        "I will not be able to come to the {appt} because of {reason}",
        "Unfortunately I cannot attend {day}",
        "I am not able to come to {place}, {reason}",
        "Won't be able to come. {reason}",
        "Sorry, cancel the {appt} please, I can't come",
        "I can't come {day} or {day2}, {reason}",
        "{greet} I am unable to attend. {close}",
        "I'm {reason2} so I can't come",
    ],
    "ar_msa": [
        "{greet} عذرا، لا أستطيع الحضور بسبب {reason}. {close}",
        "للأسف لن أتمكن من القدوم {day}",
        "لا يمكنني الحضور إلى {place}، {reason}",
        "أعتذر عن عدم الحضور. {reason}",
        "أرجو إلغاء {appt}، لن أستطيع القدوم",
        "لن أتمكن من الحضور {day} ولا {day2} بسبب {reason}",
        "أنا {reason2} ولا أستطيع الحضور",
    ],
    "ar_gulf": [
        "{greet} آسف ما اقدر أجي بسبب {reason}. {close}",
        "للأسف ما بقدر اجي {day}",
        "ما اقدر اوصل {place}، {reason}",
        "عذرا ما بحضر. {reason}",
        "الغوا {appt} لو سمحتوا، ما اقدر أجي",
        "ما اقدر اجي {day} ولا {day2} بسبب {reason}",
        "أنا {reason2} وما اقدر اجي",
    ],
    "arabizi": [
        "{greet} asif ma a2dar ajee bsabab {reason}. {close}",
        "lil asaf ma bi2dar ajee {day}",
        "ma a2dar awsal {place}, {reason}",
        "3ithran ma bahdar. {reason}",
        "il8o {appt} law samaht, ma a2dar ajee",
        "ma a2dar ajee {day} wala {day2} bsabab {reason}",
        "ana {reason2} w ma a2dar ajee",
    ],
    "mixed": [
        "sorry ما اقدر أجي {day_ar}, {reason_ar}",
        "I can't attend بسبب {reason_ar}. {close_en}",
        "please cancel {appt_en} لأني {reason2_ar}",
        "آسف, unable to come {day_en}",
        "للأسف I am {reason2_en} so ما اقدر أحضر",
    ],
}

# ---------------------------------------------------------------------------------------- wrong_number
T["wrong_number"] = {
    "en": [
        "{greet} wrong number",
        "This is the wrong number. You have the wrong person.",
        "I think you sent this to the wrong person",
        "Sorry, this number does not belong to that person anymore",
        "{greet} you have the wrong number, I don't know who this is for",
        "No one here by that name. Wrong number.",
        "I changed my number long ago, this is not their number",
        "Not me, wrong person.",
        "This message is not for me",
        "I am not the person you are looking for",
    ],
    "ar_msa": [
        "{greet} هذا رقم خاطئ",
        "الرقم خطأ، أنتم تتصلون بالشخص الخطأ",
        "يبدو أن الرسالة وصلت إلى الشخص الخطأ",
        "هذا الرقم لا يخص الشخص المطلوب",
        "لا يوجد أحد بهذا الاسم هنا، الرقم غير صحيح",
        "هذه الرسالة ليست لي",
        "لست الشخص الذي تبحثون عنه",
    ],
    "ar_gulf": [
        "{greet} الرقم غلط",
        "غلطانين في الرقم، مب أنا اللي تبونه",
        "الظاهر الرسالة وصلت لشخص غلط",
        "هذا الرقم مب حق الشخص اللي تبونه",
        "ما في أحد بهالاسم هنا، الرقم غلط",
        "هالرسالة مب لي",
        "مب أنا اللي تدورون عليه",
    ],
    "arabizi": [
        "{greet} el raqam ghalat",
        "ghalataneen fil raqam, mub ana elli tibooneh",
        "el thaher el resala wasalat la shakhs ghalat",
        "hatha el raqam mub 7a2 el shakhs elli tibooneh",
        "ma fi a7ad bhalesm hena, el raqam ghalat",
        "halresala mub li",
        "wrong number ya akhi, mub ana",
    ],
    "mixed": [
        "wrong number، الرقم غلط",
        "sorry هذا مب رقمه, wrong person",
        "this message مب لي, please check the number",
        "الرقم wrong, I don't know who you are looking for",
        "{greet_ar} you have the wrong person، مب أنا",
    ],
}

# ------------------------------------------------------------------------------------- dont_understand
T["dont_understand"] = {
    "en": [
        "{greet} I don't understand this message",
        "What is this message about? I do not understand",
        "I didn't get what you mean, can you explain in simple words",
        "Can you send it in Arabic? I can't read English",
        "Can you send this in English please, I do not understand Arabic",
        "What does this mean? Why did you message me?",
        "I did not understand, please call me",
        "Sorry, which test are you talking about? I am confused",
        "Not clear to me, please explain",
        "Who is this and what do you want from me?",
    ],
    "ar_msa": [
        "{greet} لم أفهم هذه الرسالة",
        "ما المقصود بهذه الرسالة؟ لم أفهم",
        "هل يمكن إرسالها بالعربية؟ لا أقرأ الإنجليزية",
        "هل يمكن إرسالها بالإنجليزية؟ لا أفهم العربية",
        "لماذا أرسلتم لي هذه الرسالة؟ لست متأكدا مما تقصدون",
        "لم أفهم، أرجو الاتصال بي",
        "الرسالة غير واضحة، أرجو التوضيح",
    ],
    "ar_gulf": [
        "{greet} ما فهمت الرسالة",
        "شو المقصود بهالرسالة؟ ما فهمت",
        "ممكن ترسلونها بالعربي؟ ما اقرا انجليزي",
        "ممكن ترسلونها بالانجليزي؟ ما افهم عربي",
        "ليش ارسلتوا لي هالرسالة؟ مب فاهم شو تبون",
        "ما فهمت، اتصلوا فيني",
        "الرسالة مب واضحة، وضحوا لي",
        "شنو هذا؟ مب فاهم شي",
    ],
    "arabizi": [
        "{greet} ma fhemt el resala",
        "shu el maqsood bhalresala? ma fhemt",
        "momken tersiloha bil 3arabi? ma a2ra englizi",
        "momken tersiloha bil englizi? ma afham 3arabi",
        "laish arsaltoo li halresala? mub fahem shu tibon",
        "ma fhemt, ittasilo feeni",
        "el resala mub wadha, wadhho li",
        "shino hatha? mub fahem shi",
    ],
    "mixed": [
        "ما فهمت this message, what is it?",
        "sorry I don't understand، ممكن توضحون",
        "please send in عربي, ما اقرا English",
        "شو هذا؟ I am confused about this message",
        "can you explain? الرسالة مب واضحة",
    ],
}

# ---------------------------------------------------------------------------------------------- opt_out
T["opt_out"] = {
    "en": [
        "STOP",
        "Stop messaging me",
        "Please do not contact me again",
        "{greet} remove me from your list. {close}",
        "I don't want any more messages or calls",
        "Unsubscribe",
        "Do not call me again, I do not consent to this",
        "I withdraw my consent, stop contacting me",
        "I don't want to take part in this programme anymore",
        "Leave me alone",
        "I refuse any follow up, please delete my number",
        "Please delete my data and do not contact me",
    ],
    "ar_msa": [
        "توقفوا عن مراسلتي",
        "أرجو عدم التواصل معي مرة أخرى",
        "{greet} احذفوا رقمي من القائمة. {close}",
        "لا أرغب في تلقي أي رسائل أو اتصالات بعد الآن",
        "ألغوا اشتراكي في هذه الرسائل",
        "أسحب موافقتي، لا تتصلوا بي",
        "لا أريد المشاركة في هذا البرنامج",
        "أرجو حذف بياناتي وعدم الاتصال بي",
    ],
    "ar_gulf": [
        "بطلوا ترسلون لي",
        "لا تتصلون فيني مرة ثانية",
        "{greet} شيلوا رقمي من القائمة. {close}",
        "ما ابي اي رسايل ولا اتصالات بعد",
        "الغوا اشتراكي في الرسايل هذي",
        "ارجعت موافقتي، لا تتصلون فيني",
        "ما ابي اشارك في البرنامج هذا",
        "امسحوا بياناتي ولا تتصلون فيني",
        "خلوني في حالي",
    ],
    "arabizi": [
        "battilo tersiloon li",
        "la titasiloon feeni marra thanya",
        "{greet} shayyilo raqami min el 2a2ima. {close}",
        "ma abi ayy rasa2il wala ittisalat ba3d",
        "il8o ishtirakee fil rasa2il hathi",
        "arja3t muwafa2ti, la titasiloon feeni",
        "ma abi ashark fil bernamej hatha",
        "stop sending me messages",
        "imsa7o bayanati wala titasiloon feeni",
    ],
    "mixed": [
        "STOP لا تتصلون",
        "stop ترسلون لي please",
        "unsubscribe، ما ابي رسايل",
        "I don't want any calls ولا رسايل بعد",
        "please remove my number، شيلوا رقمي",
        "ارجعت consent, do not contact me",
    ],
}

# ------------------------------------------------------------------------------------------- question
T["question"] = {
    "en": [
        "Where is {place} located?",
        "What time should I come? {close}",
        "Do I need to fast before the test?",
        "How much will it cost?",
        "Is it covered by my insurance?",
        "Is the result serious? Should I be worried?",
        "How long will the test take?",
        "Can I bring {person} with me?",
        "What documents do I need to bring?",
        "Which doctor will I see?",
        "What does my result mean?",
        "Can I take my medicine before the test?",
        "Is parking available at {place}?",
    ],
    "ar_msa": [
        "أين يقع {place}؟",
        "في أي وقت يجب أن أحضر؟ {close}",
        "هل يجب أن أصوم قبل الفحص؟",
        "كم تبلغ التكلفة؟",
        "هل يغطي التأمين هذا الفحص؟",
        "هل النتيجة خطيرة؟ هل يجب أن أقلق؟",
        "كم تستغرق مدة الفحص؟",
        "هل يمكنني إحضار {person} معي؟",
        "ما المستندات المطلوبة؟",
        "ماذا تعني نتيجتي؟",
        "هل يمكنني تناول دوائي قبل الفحص؟",
    ],
    "ar_gulf": [
        "وين موقع {place}؟",
        "أي وقت لازم اجي؟ {close}",
        "لازم اصوم قبل الفحص؟",
        "كم بتكلف؟",
        "التأمين يغطي الفحص هذا؟",
        "النتيجة خطيرة؟ لازم اخاف؟",
        "كم ياخذ الفحص من وقت؟",
        "اقدر اجيب {person} معاي؟",
        "شو الاوراق اللي لازم اجيبها؟",
        "شو تعني نتيجتي؟",
        "اقدر اخذ دوايي قبل الفحص؟",
        "في موقف سيارات عند {place}؟",
    ],
    "arabizi": [
        "wein mawqi3 {place}?",
        "ayy waqt lazim ajee? {close}",
        "lazim asoum 9abl el fahs?",
        "kam bitkallef?",
        "el ta2meen yughatti el fahs hatha?",
        "el natija khatira? lazim akhaf?",
        "kam yakhith el fahs min waqt?",
        "a2dar ajeeb {person} ma3aya?",
        "shu el awra2 elli lazim ajeebha?",
        "shu ta3ni natijati?",
        "a2dar akhith dawayi 9abl el fahs?",
        "fi mawqif sayarat 3ind {place}?",
    ],
    "mixed": [
        "where is {place_ar}؟",
        "do I need to صيام before the test?",
        "كم the cost? {close_en}",
        "is it covered بالتأمين؟",
        "what time أجي {day_ar}?",
        "هل النتيجة serious? please tell me",
    ],
}

# ---------------------------------------------------------------------------------------- already_done
T["already_done"] = {
    "en": [
        "I already did this test {when}",
        "I have already done the colonoscopy",
        "Already done, I did it {when} at another hospital",
        "I did the camera test {when}, no need",
        "Done already. {close}",
        "I went to my doctor and he already did it",
        "I completed the test {when} at {place}",
        "Already booked and done with my private doctor",
        "I already had a colonoscopy {when}",
    ],
    "ar_msa": [
        "لقد أجريت هذا الفحص {when}",
        "أجريت تنظيرا للقولون {when}",
        "تم الفحص مسبقا في مستشفى آخر",
        "أنا أجريته بالفعل {when}، لا حاجة لذلك",
        "فحصت عند طبيبي الخاص {when}",
        "تم، لقد انتهيت من الفحص {when}",
        "أجريت الفحص بالفعل. {close}",
    ],
    "ar_gulf": [
        "سويت الفحص هذا {when}",
        "سويت منظار القولون {when}",
        "الفحص انسوى من قبل في مستشفى ثاني",
        "سويته من قبل {when}، ما في حاجة",
        "فحصت عند دكتوري الخاص {when}",
        "خلصت الفحص {when}",
        "سويته خلاص. {close}",
    ],
    "arabizi": [
        "sawwayt el fahs hatha {when}",
        "sawwayt mindhar el qolon {when}",
        "el fahs insawwa men 9abl fi mustashfa thani",
        "sawwaytah men 9abl {when}, ma fi 7aja",
        "fa7ast 3ind doctori el khass {when}",
        "khallast el fahs {when}",
        "sawwaytah khalas. {close}",
        "already did it {when}",
    ],
    "mixed": [
        "I already did it {when_en}، خلصت الفحص",
        "سويت colonoscopy {when_ar}",
        "done already، سويته عند دكتوري",
        "already تم الفحص {when_ar}",
        "no need، I did the test {when_en}",
    ],
}

# ------------------------------------------------------------------------------------------------ other
T["other"] = {
    "en": [
        "{greet}",
        "{other}",
        "Thanks",
        "Thank you so much",
        "Let me think about it",
        "Maybe",
        "I will let you know later",
        "Do you have any offers on vitamins?",
        "test",
        "Please pray for my mother",
        "Who won the match yesterday?",
        "{greet} {other}",
        "Send me the discount code",
    ],
    "ar_msa": [
        "{greet}",
        "{other}",
        "شكرا",
        "شكرا جزيلا لكم",
        "سأفكر في الأمر",
        "ربما",
        "سأخبركم لاحقا",
        "هل لديكم عروض على الفيتامينات؟",
        "اختبار",
        "{greet} {other}",
        "أرسلوا لي رمز الخصم",
    ],
    "ar_gulf": [
        "{greet}",
        "{other}",
        "مشكورين",
        "تسلمون والله",
        "خلني افكر",
        "يمكن",
        "بخبركم عقب",
        "عندكم عروض على الفيتامينات؟",
        "تجربة",
        "{greet} {other}",
        "ارسلوا لي كود الخصم",
        "الله يعطيكم العافية",
    ],
    "arabizi": [
        "{greet}",
        "{other}",
        "shukran",
        "mashkooreen wallah",
        "khalleeni afakker",
        "yimkin",
        "bakhbirkum 3a2ib",
        "3indkum 3uroodh 3ala el vitamins?",
        "test",
        "{greet} {other}",
        "irsilo li code el khasm",
    ],
    "mixed": [
        "{greet_ar} hello",
        "thanks، مشكورين",
        "maybe، يمكن",
        "ok شكرا",
        "hi هلا",
        "good morning صباح الخير",
    ],
}

TEMPLATES = T

# Messages made of an emoji only (or nearly): the label is whatever a reasonable person would give them.
EMOJI_ONLY = {
    "confirm": ["👍", "👍🏽", "✅", "🙏👍"],
    "other": ["🙏", "😊", "❤️", "🌹", "🤲"],
}

# Sentences that start with one intent and end with another. The label is the safer one for the patient
# (opt-out beats everything, wrong number beats the rest): the triage must hand these to a person.
COMPOUND = {
    "opt_out": {
        "en": ["{ok}, I can come {day}, but stop messaging me after this", "I understand the message. Please do not contact me again",
               "Thanks for the reminder. Remove me from your list"],
        "ar_gulf": ["{ok} بجي {day}، بس بعدها لا تتصلون فيني", "وصلت الرسالة. شيلوا رقمي من القائمة",
                    "مشكورين على التذكير. ما ابي رسايل بعد"],
        "ar_msa": ["{ok}، سأحضر {day}، لكن أرجو عدم التواصل معي بعد ذلك", "استلمت الرسالة. احذفوا رقمي من القائمة"],
        "arabizi": ["{ok} bajee {day}, bas ba3dha la titasiloon feeni", "wasalat el resala. shayyilo raqami"],
    },
    "wrong_number": {
        "en": ["{ok}, wrong number, not me", "Thanks but this is the wrong person"],
        "ar_gulf": ["{ok} بس الرقم غلط، مب أنا", "مشكورين بس الرسالة لشخص غلط"],
        "ar_msa": ["شكرا، لكن هذا رقم خاطئ", "{ok}، لكن الرسالة ليست لي"],
        "arabizi": ["{ok} bas el raqam ghalat", "shukran bas el resala la shakhs ghalat"],
    },
}
