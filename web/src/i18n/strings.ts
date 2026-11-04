import { PROGRAMME_LABOUR } from "../lib/programmeFigures";
/**
 * Patient-facing strings, English and Arabic. Grade-6 reading level: short, plain sentences.
 *
 * TODO: native-speaker review - every Arabic string below was written by the engineering team,
 * not yet reviewed by a native Arabic speaker (plan, Week 3: "Arabic speaker review of all
 * patient-facing strings"). Do not ship to patients before that review. Use Modern Standard
 * Arabic that is easy to read; avoid dialect. Check gender-neutral phrasing and medical terms
 * (تنظير القولون = colonoscopy) with the reviewer.
 *
 * E14, non-disclosure: no string states or hints at what the screening found, names a disease or
 * reassures. IFHAS: abnormal results are communicated with the participant present, so the page
 * says only that the result is ready and the care team will explain it in person or by phone.
 * The colonoscopy may be named as the next appointment (the standard concerns the result, not the
 * procedure). Keep it neutral.
 */
import type { Lang } from "../api/contract";

/** First-strong isolate so a Latin name does not scramble neighbouring Arabic text. */
const iso = (s: string) => `\u2068${s}\u2069`;

/** Pathway-specific patient wording (patient app): the pathway's own words for its next step, never a finding (E14). */
export interface PathwayStrings {
  /** Three short steps under "My next step" in the plain explainer. */
  nextSteps: string[];
  /** The four-step journey strip: result received, care team ready, the appointment, the review. */
  progress: string[];
  /** Heading of the appointment card. */
  appointmentTitle: string;
  /** Title of the calendar entry (.ics) the patient saves. */
  calendarTitle: string;
  /** Shown while the care team is arranging the appointment. */
  waiting: string;
}

export interface PatientStrings {
  appTitle: string;
  language: string;
  switchTo: string;
  loading: string;
  loadError: string;
  offline: string;
  offlineAction: string;
  navLabel: string;
  navHome: string;
  navPrivacy: string;
  resultHeading: string;
  resultBody: string;
  explainInPerson: string;
  nextHeading: string;
  /** What "My next step" says in each situation; the single primary action sits under it. */
  nextState: { preContact: string; contacted: string; booked: string; done: string; closed: string; withdrawn: string; working: string };
  /** Pathway wording, by the pathway pack's name; `other` for a pack the app does not know. */
  pathways: { colorectal_fit: PathwayStrings; cervical_hpv: PathwayStrings; chronic_conditions: PathwayStrings; other: PathwayStrings };
  appointmentHeading: string;
  appointmentTimeLater: string;
  addToCalendar: string;
  calendarNote: string;
  calendarDescription: string;
  reschedule: string;
  rescheduleDone: string;
  cannotAttend: string;
  cannotAttendDone: string;
  ownerHeading: string;
  ownerLine: (name: string, role: string) => string;
  ownerPending: string;
  ownerReach: string;
  callCareTeam: string;
  demoNumber: string;
  /** Plain names for the roles a patient is told about (the owner, and who looked at the record). */
  roles: { primary_care_owner: string; specialist_owner: string; coordinator: string; supervisor: string; lab: string; other: string };
  /**
   * SEC-2: when the API sends no name, the owner is shown by role only. One full sentence per backend role
   * (`primary_care_owner`, `specialist_owner`, `coordinator`, `supervisor`), and `other` for any role not listed.
   */
  ownerByRole: { primary_care_owner: string; specialist_owner: string; coordinator: string; supervisor: string; other: string };
  understand: string;
  understandDone: string;
  understandWait: string;
  dontUnderstand: string;
  callbackDone: string;
  historyHeading: string;
  historyEmpty: string;
  history: {
    case_created: string; acknowledge: string; accept_ownership: string; contact_patient: string; patient_acknowledge: string;
    book_confirmatory: string; complete_confirmatory: string; clinician_review: string; close: string; reopen: string;
    consent_updated: string; callback_requested: string; message_sent: string;
  };
  privacyHeading: string;
  privacyIntro: string;
  /** "How we prepare messages": plain, non-disclosing notice that software helps and a person approves (W-AI). */
  aiNoticeHeading: string;
  aiNoticeSoftware: string;
  aiNoticeStaff: string;
  aiNoticePerson: string;
  aiNoticeStop: string;
  dataHeading: string;
  data: string[];
  consentHeading: string;
  channelLabel: string;
  channels: { phone: string; sms: string; whatsapp: string; letter: string };
  shareLabel: string;
  languageLabel: string;
  languageNote: string;
  withdrawButton: string;
  withdrawHint: string;
  withdrawnNotice: string;
  withdrawConfirmTitle: string;
  withdrawConfirmBody: string;
  withdrawConfirmYes: string;
  withdrawConfirmNo: string;
  resumeButton: string;
  saved: string;
  saveFailed: string;
  actionFailed: string;
  accessHeading: string;
  accessIntro: string;
  accessEmpty: string;
  accessNote: string;
  accessLoadError: string;
  deviceHeading: string;
  deviceBody: string;
  deviceClear: string;
  deviceCleared: string;
  progressHeading: string;
  homeTitle: string;
  homeBody: string;
}

export const STRINGS: Record<Lang, PatientStrings> = {
  en: {
    appTitle: "Your screening result",
    language: "Language",
    switchTo: "العربية",
    loading: "Loading...",
    loadError: "We could not load your information. Please try again.",
    offline: "You are offline. This is the last information we saved.",
    offlineAction: "You are offline. You can read what is saved. You can send something when you are back online.",
    navLabel: "Main menu",
    navHome: "My care",
    navPrivacy: "Privacy",
    resultHeading: "My result",
    resultBody:
      "Your screening result is ready. Your care team needs to talk with you about it and about the next step.",
    explainInPerson: "Your care team will explain your result in person or by phone.",
    nextHeading: "My next step",
    nextState: {
      preContact: "Your care team is getting ready to contact you. You do not need to do anything yet.",
      contacted: "Your care team sent you a message. Tap the button when you have read it.",
      booked: "Your appointment is booked. Please check the date and add it to your calendar.",
      done: "Your appointment is done. A doctor from your care team will go over it with you.",
      closed: "Your care team has finished this step. Call your care team if you have a question.",
      withdrawn: "You asked us to stop contact. You can allow contact again in Privacy.",
      working: "Your care team is working on your next step. Call your care team if you have a question.",
    },
    pathways: {
      colorectal_fit: {
        nextSteps: [
          "Your care team will talk with you about your result.",
          "The next step is an appointment for a camera test called a colonoscopy.",
          // AI clinical review 2026-10-18
          "You will get a date and clear steps to get ready.",
        ],
        progress: ["Result received", "Care team ready", "Camera test", "Doctor review"],
        appointmentTitle: "Your camera test (colonoscopy)",
        calendarTitle: "Camera test (colonoscopy)",
        waiting: "Your care team is arranging your camera test. You will get a date soon.",
      },
      cervical_hpv: {
        nextSteps: [
          "Your care team will talk with you about your result.",
          "The next step is an appointment for a follow-up examination.",
          "You will get a date and clear steps to get ready.",
        ],
        progress: ["Result received", "Care team ready", "Follow-up examination", "Doctor review"],
        appointmentTitle: "Your follow-up examination",
        calendarTitle: "Follow-up examination",
        waiting: "Your care team is arranging your follow-up examination. You will get a date soon.",
      },
      chronic_conditions: {
        nextSteps: [
          "Your care team will talk with you about your result.",
          "The next step is a visit with your family doctor.",
          "You will get a date and clear steps to get ready.",
        ],
        progress: ["Result received", "Care team ready", "Family doctor visit", "Care plan"],
        appointmentTitle: "Your visit with your family doctor",
        calendarTitle: "Visit with your family doctor",
        waiting: "Your care team is arranging your visit with your family doctor. You will get a date soon.",
      },
      other: {
        nextSteps: [
          "Your care team will talk with you about your result.",
          "The next step is an appointment.",
          "You will get a date and clear steps to get ready.",
        ],
        progress: ["Result received", "Care team ready", "Appointment", "Doctor review"],
        appointmentTitle: "Your appointment",
        calendarTitle: "Appointment",
        waiting: "Your care team is arranging your appointment. You will get a date soon.",
      },
    },
    appointmentHeading: "My appointment",
    appointmentTimeLater: "Your care team will tell you the time.",
    addToCalendar: "Add to my calendar",
    calendarNote: "This saves a small file on your phone. Open it to add the date to your calendar.",
    calendarDescription: "Booked by your care team. Please call your care team if you cannot come.",
    reschedule: "Ask for another date",
    rescheduleDone: "Thank you. A person from your care team will call you about a new date.",
    cannotAttend: "I cannot come",
    cannotAttendDone: "Thank you. A person from your care team will call you.",
    ownerHeading: "Who is looking after you",
    ownerLine: (name, role) => `${iso(name)} (${role}) is looking after your care. You can ask them any question.`,
    // AI clinical review 2026-10-18
    ownerPending: "A person in your care team will be chosen to look after you soon.",
    ownerReach: "How to reach your care team",
    callCareTeam: "Call your care team",
    demoNumber: "Demo number. All data here is synthetic.",
    roles: {
      primary_care_owner: "Primary care doctor",
      specialist_owner: "Specialist doctor",
      coordinator: "Care coordinator",
      supervisor: "Senior member of the care team",
      lab: "Laboratory team",
      other: "Care team",
    },
    ownerByRole: {
      primary_care_owner: "Your primary care doctor is looking after your care. You can ask them any question.",
      specialist_owner: "Your specialist doctor is looking after your care. You can ask them any question.",
      coordinator: "Your care coordinator is looking after your care. You can ask them any question.",
      supervisor: "A senior member of your care team is looking after your care. You can ask them any question.",
      other: "Your care team is looking after your care. You can ask them any question.",
    },
    understand: "I understand, please contact me",
    understandDone: "Thank you. We saved this. Your care team will contact you.",
    understandWait: "You can tap this button after your care team has sent you a message.",
    dontUnderstand: "I don't understand, call me",
    callbackDone: "Thank you. A person from your care team will call you.",
    historyHeading: "What has happened",
    historyEmpty: "Nothing yet.",
    history: {
      case_created: "Your result was received.",
      acknowledge: "Your care team saw your result.",
      accept_ownership: "A member of your care team took charge of your care.",
      contact_patient: "Your care team contacted you.",
      patient_acknowledge: "You said you understood.",
      book_confirmatory: "Your appointment was booked.",
      complete_confirmatory: "Your appointment took place.",
      clinician_review: "A doctor went over it.",
      close: "Your care team finished this step.",
      reopen: "Your care team opened this step again.",
      consent_updated: "You changed your contact choices.",
      callback_requested: "You asked for a call.",
      message_sent: "We sent you a message.",
    },
    privacyHeading: "Privacy and choices",
    privacyIntro: "You decide how we contact you. You can see who opened your record. You can change your mind at any time.",
    aiNoticeHeading: "How we prepare messages",
    aiNoticeSoftware: "We use software to help prepare our messages to you.",
    aiNoticeStaff: "A member of staff checks and approves each message before we send it.",
    aiNoticePerson: "To speak to a person, press \"I don't understand, call me\". You can also call your care team.",
    aiNoticeStop: "You can stop messages at any time. Use \"Stop all contact\" in \"Your choices\" below.",
    dataHeading: "How we use your data",
    data: [
      "This is a demonstration. All the information here is synthetic. It is not about a real person.",
      "Software only prepares and sorts messages. It never decides anything about your care.",
      "A person on your care team makes every decision about your care.",
      "We keep only what we need. What you write to us is kept apart from your care record, and it can be erased.",
      "Your care record lists steps, dates and roles. It does not hold names, phone numbers or free text.",
      "When your case is finished, you can ask for your personal data to be erased. Two supervisors must agree.",
    ],
    consentHeading: "Your choices",
    channelLabel: "How may we contact you?",
    channels: { phone: "Phone call", sms: "Text message", whatsapp: "WhatsApp", letter: "Letter" },
    shareLabel: "My care team may share my result with the specialist who will do my test.",
    languageLabel: "Language for this app and your messages",
    languageNote: "Kept on this phone and told to your care team. We use it for the messages we send you.",
    withdrawButton: "Stop all contact",
    // AI clinical review 2026-10-18
    withdrawHint: "If you stop contact, your care team will not message or call you. A person will check your case. You can still call your care team.",
    // AI clinical review 2026-10-18
    withdrawnNotice: "You asked us to stop contact. We will not message or call you. A person on your care team will check your case. You can still call your care team.",
    withdrawConfirmTitle: "Stop all contact?",
    withdrawConfirmBody: "Your care team will not message or call you. A person will check your case. You can turn contact back on here at any time.",
    withdrawConfirmYes: "Yes, stop all contact",
    withdrawConfirmNo: "No, keep contact",
    resumeButton: "You may contact me again",
    saved: "Saved",
    saveFailed: "We could not save this. Please try again.",
    actionFailed: "That did not work. Please try again or call your care team.",
    accessHeading: "Who looked at my record",
    accessIntro: "Each time a member of staff opens your record, it is written here with their name, their role and the time.",
    accessEmpty: "Nobody on your care team has opened your record yet.",
    accessNote: "We list each person once every 15 minutes, even if they look more than once. Computer systems that keep time or send reminders are not listed.",
    accessLoadError: "We could not load this list. Please try again.",
    deviceHeading: "On this phone",
    deviceBody: "To work without internet, this phone keeps the last information it loaded. It stays on this phone only.",
    deviceClear: "Remove saved information from this phone",
    deviceCleared: "Removed. Open the link from your care team again to load your page.",
    progressHeading: "Where you are now",
    homeTitle: "Your screening result",
    // AI clinical review 2026-10-18
    homeBody: "Open the link your care team sent you to see your next steps. You can add this page to your home screen.",
  },
  // AI Arabic review 2026-10-18 (AI-assisted, not a native-speaker human review; H2 still pending).
  // Record: docs/governance/arabic-review.md. Terms: فحص الكشف المبكر (screening test), فحص بالكاميرا
  // (camera test) with تنظير القولون named once, فريق رعايتك (care team), موعد, نتيجة, الشخص المسؤول عن
  // رعايتك (care owner). No form that changes with the patient's gender (no افتح / ستحصل / أنت غير متصل), and
  // حالتك (your condition) replaced by رعايتك or ملفك so no line hints at an illness. Tanween ـاً, Arabic comma.
  // Never the substring دم anywhere on the page (web/tests/patient.test.tsx).
  // Patient app and privacy centre (2026-11-05): written by the engineering team, same rules, NOT reviewed by a native
  // speaker (H2 pending). They are in the Arabic review pack (tools/review/build_packs.py) like every other line here.
  ar: {
    appTitle: "نتيجة فحص الكشف المبكر", // AI Arabic review 2026-10-18: "screening" named, as in the English
    language: "اللغة",
    switchTo: "English",
    loading: "جارٍ التحميل...",
    loadError: "لم نتمكن من تحميل معلوماتك. يرجى المحاولة مرة أخرى.",
    offline: "لا يوجد اتصال بالإنترنت. هذه آخر معلومات حفظناها.", // AI Arabic review 2026-10-18: gender-neutral
    offlineAction: "لا يوجد اتصال بالإنترنت. يمكنك قراءة المعلومات المحفوظة. يمكنك الإرسال عند عودة الاتصال.",
    navLabel: "القائمة الرئيسية",
    navHome: "رعايتي",
    navPrivacy: "الخصوصية",
    resultHeading: "نتيجتي",
    resultBody:
      "نتيجة فحص الكشف المبكر جاهزة. يحتاج فريق رعايتك إلى التحدث معك عنها وعن الخطوة التالية.", // AI Arabic review 2026-10-18
    explainInPerson: "سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف.", // AI Arabic review 2026-10-18: tanween ـاً
    nextHeading: "خطوتي التالية",
    nextState: {
      preContact: "يستعد فريق رعايتك للتواصل معك. لا تحتاج إلى فعل شيء الآن.",
      contacted: "أرسل لك فريق رعايتك رسالة. اضغط على الزر بعد قراءتها.",
      booked: "تم حجز موعدك. يرجى التأكد من التاريخ وإضافته إلى التقويم.",
      done: "تم الموعد. سيراجع معك طبيب من فريق رعايتك ما جرى.",
      closed: "أنهى فريق رعايتك هذه الخطوة. اتصل بفريق رعايتك إذا كان لديك سؤال.",
      withdrawn: "طلبت إيقاف التواصل. يمكنك السماح بالتواصل من جديد في قسم الخصوصية.",
      working: "يعمل فريق رعايتك على خطوتك التالية. اتصل بفريق رعايتك إذا كان لديك سؤال.",
    },
    pathways: {
      colorectal_fit: {
        nextSteps: [
          "سيتحدث معك فريق رعايتك عن نتيجتك.",
          "الخطوة التالية هي موعد لفحص بالكاميرا اسمه تنظير القولون.",
          "سيصلك موعد وخطوات سهلة للتحضير.", // AI Arabic review 2026-10-18: gender-neutral (was ستحصل)
        ],
        progress: ["وصلت النتيجة", "فريق رعايتك جاهز", "الفحص بالكاميرا", "مراجعة الطبيب"], // AI Arabic review 2026-10-18
        appointmentTitle: "فحصك بالكاميرا (تنظير القولون)",
        calendarTitle: "فحص بالكاميرا (تنظير القولون)",
        waiting: "يجهّز فريق رعايتك موعد الفحص بالكاميرا. سيصلك التاريخ قريباً.",
      },
      cervical_hpv: {
        nextSteps: [
          "سيتحدث معك فريق رعايتك عن نتيجتك.",
          "الخطوة التالية هي موعد لفحص متابعة في العيادة.",
          "سيصلك موعد وخطوات سهلة للتحضير.",
        ],
        progress: ["وصلت النتيجة", "فريق رعايتك جاهز", "فحص المتابعة", "مراجعة الطبيب"],
        appointmentTitle: "فحص المتابعة في العيادة",
        calendarTitle: "فحص متابعة في العيادة",
        waiting: "يجهّز فريق رعايتك موعد فحص المتابعة. سيصلك التاريخ قريباً.",
      },
      chronic_conditions: {
        nextSteps: [
          "سيتحدث معك فريق رعايتك عن نتيجتك.",
          "الخطوة التالية هي زيارة طبيب الأسرة.",
          "سيصلك موعد وخطوات سهلة للتحضير.",
        ],
        progress: ["وصلت النتيجة", "فريق رعايتك جاهز", "زيارة طبيب الأسرة", "خطة الرعاية"],
        appointmentTitle: "زيارتك لطبيب الأسرة",
        calendarTitle: "زيارة طبيب الأسرة",
        waiting: "يجهّز فريق رعايتك موعد زيارتك لطبيب الأسرة. سيصلك التاريخ قريباً.",
      },
      other: {
        nextSteps: [
          "سيتحدث معك فريق رعايتك عن نتيجتك.",
          "الخطوة التالية هي موعد.",
          "سيصلك موعد وخطوات سهلة للتحضير.",
        ],
        progress: ["وصلت النتيجة", "فريق رعايتك جاهز", "الموعد", "مراجعة الطبيب"],
        appointmentTitle: "موعدك",
        calendarTitle: "موعد",
        waiting: "يجهّز فريق رعايتك موعدك. سيصلك التاريخ قريباً.",
      },
    },
    appointmentHeading: "موعدي",
    appointmentTimeLater: "سيخبرك فريق رعايتك بالوقت.",
    addToCalendar: "أضف الموعد إلى التقويم",
    calendarNote: "يحفظ هذا ملفاً صغيراً على هاتفك. افتح الملف لإضافة التاريخ إلى التقويم.",
    calendarDescription: "حجزه فريق رعايتك. يرجى الاتصال بفريق رعايتك إذا لم تتمكن من الحضور.",
    reschedule: "اطلب تاريخاً آخر",
    rescheduleDone: "شكراً لك. سيتصل بك أحد أفراد فريق رعايتك بخصوص تاريخ جديد.",
    cannotAttend: "لا أستطيع الحضور",
    cannotAttendDone: "شكراً لك. سيتصل بك أحد أفراد فريق رعايتك.",
    ownerHeading: "من يتابعك", // AI Arabic review 2026-10-18: matches the English; no حالتك
    // AI Arabic review 2026-10-18: الشخص المسؤول عن رعايتك (care owner term); the colon avoids هو/هي before the name.
    ownerLine: (name, role) => `الشخص المسؤول عن رعايتك:\n${iso(name)} (${role}).\nيمكنك طرح أي سؤال.`,
    ownerPending: "سنعيّن لك منسق رعاية قريباً.",
    ownerReach: "كيف تتواصل مع فريق رعايتك",
    callCareTeam: "اتصل بفريق رعايتك",
    demoNumber: "رقم تجريبي. كل البيانات هنا بيانات اصطناعية.",
    roles: {
      primary_care_owner: "طبيب الرعاية الأولية",
      specialist_owner: "الطبيب الأخصائي",
      coordinator: "منسق الرعاية",
      supervisor: "أحد كبار أعضاء فريق الرعاية",
      lab: "فريق المختبر",
      other: "فريق الرعاية",
    },
    // AI Arabic review 2026-10-18: يتابعك (looks after you) for every role; عليه dropped so it suits a woman owner.
    ownerByRole: {
      primary_care_owner: "يتابعك طبيبك في الرعاية الأولية. يمكنك طرح أي سؤال.",
      specialist_owner: "يتابعك طبيبك الأخصائي. يمكنك طرح أي سؤال.",
      coordinator: "يتابعك منسق الرعاية الخاص بك. يمكنك طرح أي سؤال.",
      supervisor: "يتابعك أحد كبار أعضاء فريق رعايتك. يمكنك طرح أي سؤال.",
      other: "يتابعك فريق رعايتك. يمكنك طرح أي سؤال.",
    },
    understand: "فهمت، يرجى التواصل معي",
    understandDone: "شكراً لك. سجلنا طلبك. سيتواصل معك فريق رعايتك.", // AI Arabic review 2026-10-18: care-team term
    understandWait: "يمكنك الضغط على هذا الزر بعد أن يرسل لك فريق رعايتك رسالة.", // AI Arabic review 2026-10-18
    dontUnderstand: "لا أفهم، يرجى الاتصال بي",
    callbackDone: "شكراً لك. سيتصل بك أحد أفراد فريق رعايتك.", // AI Arabic review 2026-10-18
    historyHeading: "ما الذي جرى",
    historyEmpty: "لا شيء بعد.",
    history: {
      case_created: "وصلت نتيجتك.",
      acknowledge: "اطلع فريق رعايتك على نتيجتك.",
      accept_ownership: "تولى أحد أفراد فريق رعايتك متابعتك.",
      contact_patient: "تواصل معك فريق رعايتك.",
      patient_acknowledge: "أخبرتنا أنك فهمت.",
      book_confirmatory: "تم حجز موعدك.",
      complete_confirmatory: "تم موعدك.",
      clinician_review: "راجع طبيب ما جرى.",
      close: "أنهى فريق رعايتك هذه الخطوة.",
      reopen: "فتح فريق رعايتك هذه الخطوة من جديد.",
      consent_updated: "غيّرت اختياراتك للتواصل.",
      callback_requested: "طلبت أن نتصل بك.",
      message_sent: "أرسلنا لك رسالة.",
    },
    privacyHeading: "الخصوصية والاختيارات",
    privacyIntro: "أنت من يقرر كيف نتواصل معك. يمكنك معرفة من اطلع على ملفك. يمكنك تغيير رأيك في أي وقت.",
    // Plain Modern Standard Arabic, mirrors the English. Not reviewed by a native speaker (H2 pending). No substring دم.
    aiNoticeHeading: "كيف نُعِدّ الرسائل",
    aiNoticeSoftware: "نستعين ببرنامج حاسوبي في إعداد رسائلنا إليك.",
    aiNoticeStaff: "يراجع أحد أفراد فريقنا كل رسالة ويوافق عليها قبل إرسالها.",
    aiNoticePerson: "للتحدث مع شخص، يرجى الضغط على «لا أفهم، يرجى الاتصال بي». ويمكنك أيضاً الاتصال بفريق رعايتك.",
    aiNoticeStop: "يمكنك إيقاف الرسائل في أي وقت. يمكنك ذلك من خلال «أوقفوا كل تواصل معي» في قسم «اختياراتك» أدناه.",
    dataHeading: "كيف نتعامل مع بياناتك",
    data: [
      "هذا عرض تجريبي. كل المعلومات هنا اصطناعية. وليست عن شخص حقيقي.",
      "البرنامج الحاسوبي يُعِدّ الرسائل ويرتبها فقط. ولا يقرر شيئاً عن رعايتك.",
      "أحد أفراد فريق رعايتك هو من يتخذ كل قرار عن رعايتك.",
      "نحتفظ بما نحتاج إليه فقط. ما تكتبه لنا يُحفظ بعيداً عن سجل رعايتك، ويمكن مسحه.",
      "يسرد سجل رعايتك الخطوات والتواريخ والأدوار. ولا يحتوي على أسماء أو أرقام هواتف أو نصوص حرة.",
      "عند انتهاء حالة رعايتك، يمكنك طلب مسح بياناتك الشخصية. ويجب أن يوافق مشرفان.",
    ],
    consentHeading: "اختياراتك",
    channelLabel: "كيف يمكننا التواصل معك؟",
    channels: { phone: "مكالمة هاتفية", sms: "رسالة نصية", whatsapp: "واتساب", letter: "رسالة مكتوبة" },
    // AI Arabic review 2026-10-18: "my test" is the camera test here, not the screening test.
    shareLabel: "أسمح لفريق رعايتي بمشاركة نتيجتي مع الطبيب الأخصائي الذي سيجري لي الفحص بالكاميرا.",
    languageLabel: "لغة هذا التطبيق ورسائلك",
    languageNote: "تُحفظ على هذا الهاتف ونخبر بها فريق رعايتك. نستعملها في الرسائل التي نرسلها إليك.",
    withdrawButton: "أوقفوا كل تواصل معي", // AI Arabic review 2026-10-18: pairs with resumeButton
    withdrawHint:
      "إذا أوقفت التواصل، فلن يرسل لك فريق رعايتك رسائل ولن يتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك.", // AI Arabic review 2026-10-18
    withdrawnNotice:
      "طلبت إيقاف التواصل. لن نرسل لك رسائل ولن نتصل بك. سيراجع أحد أفراد فريق رعايتك ملفك.", // AI Arabic review 2026-10-18
    withdrawConfirmTitle: "هل تريد إيقاف كل تواصل؟",
    withdrawConfirmBody: "لن يرسل لك فريق رعايتك رسائل ولن يتصل بك. سيراجع أحد أفراد الفريق ملفك. يمكنك إعادة التواصل من هنا في أي وقت.",
    withdrawConfirmYes: "نعم، أوقفوا كل تواصل",
    withdrawConfirmNo: "لا، أبقوا التواصل",
    resumeButton: "يمكنكم التواصل معي مرة أخرى",
    saved: "تم الحفظ",
    saveFailed: "لم نتمكن من الحفظ. يرجى المحاولة مرة أخرى.",
    actionFailed: "لم تنجح العملية. يرجى المحاولة مرة أخرى أو الاتصال بفريق رعايتك.", // AI Arabic review 2026-10-18
    accessHeading: "من اطلع على ملفي",
    accessIntro: "في كل مرة يفتح أحد العاملين ملفك، يُكتب هنا اسمه ودوره والوقت.",
    accessEmpty: "لم يفتح أحد من فريق رعايتك ملفك بعد.",
    accessNote: "نسجل كل شخص مرة واحدة كل ١٥ دقيقة حتى لو نظر أكثر من مرة. الأنظمة الحاسوبية التي تحسب الوقت أو ترسل التذكيرات لا تظهر هنا.",
    accessLoadError: "لم نتمكن من تحميل هذه القائمة. يرجى المحاولة مرة أخرى.",
    deviceHeading: "على هذا الهاتف",
    deviceBody: "ليعمل التطبيق بلا إنترنت، يحتفظ هذا الهاتف بآخر معلومات حمّلها. وتبقى على هذا الهاتف فقط.",
    deviceClear: "احذف المعلومات المحفوظة من هذا الهاتف",
    deviceCleared: "تم الحذف. افتح الرابط الذي أرسله فريق رعايتك من جديد لتحميل صفحتك.",
    progressHeading: "أين وصلت الآن", // AI Arabic review 2026-10-18: progress, not a place
    homeTitle: "نتيجة فحص الكشف المبكر", // AI Arabic review 2026-10-18
    // AI Arabic review 2026-10-18: يرجى فتح / لرؤية instead of افتح / لترى (same for men and women).
    homeBody: "يرجى فتح الرابط الذي أرسله لك فريق رعايتك لرؤية نتيجتك. يمكنك إضافة هذه الصفحة إلى الشاشة الرئيسية.",
  },
};

export const DIR: Record<Lang, "ltr" | "rtl"> = { en: "ltr", ar: "rtl" };

/** Patient-facing role names (case owners' roles are free text in the log; we translate the known ones). */
const ROLE_AR: Record<string, string> = {
  "Primary care physician": "طبيب الرعاية الأولية",
  "Care coordinator": "منسق الرعاية",
};
export function roleLabel(role: string, lang: Lang): string {
  return lang === "ar" ? ROLE_AR[role] ?? role : role;
}

/**
 * Staff-console strings for the automation features (A21): the "Automated" chip, the closure-proposed banner, the per-case
 * effort counter, the dashboard's human-effort card and the human-review reasons. The staff console is English only (the
 * rest of it is inline English too), so there is no Arabic here and nothing for the native-speaker review above; nothing in
 * this block is shown to a patient. Plain, calm wording: the system proposes, a person decides.
 */
export const STAFF = {
  automatedChip: "Automated",
  automatedChipLabel: "Automated action: done by the system, not by a person",
  modelChip: "AI model",
  modelChipLabel: "Written by an AI model; a person must approve it before it is used",
  serviceChip: "Service",
  serviceChipLabel: "Done by a background service (scheduler, watchdog or notifier)",
  templateMessageSent: "Template message sent to the patient",
  assignOwnership: "Case assigned to an owner (not yet accepted)",
  closureProposed: "Closure proposed from the colonoscopy evidence",
  autoHandoff: "Hand-off requested automatically (re-route)",
  reviewReasons: {
    consent_withdrawn: "Flagged for human review (consent withdrawn)",
    reroute_exhausted: "Reroute list exhausted: a coordinator must reassign",
    incomplete_procedure: "The colonoscopy record is incomplete: it cannot close the case, a person must decide the next step",
    opt_out: "The patient opted out: automatic messages are held until a person reviews consent",
    wrong_number: "The number is wrong: automatic messages are held until a person confirms the contact details",
    triage_uncertain: "The reply could not be read with confidence: a person must read it and decide",
    already_done: "The patient says the colonoscopy is already done: a person must check and confirm",
  },
  incompleteProcedureHelp: "A colonoscopy counts as closure evidence only when a final report states it was complete (or a CT colonography was done). Arrange a repeat colonoscopy or a CT colonography, or record the complete result.",
  /** D13: the coordinator records what the patient wrote back; the triage reads it and only ever flags or relays. */
  replyForm: {
    heading: "Record patient reply",
    intro: "The patient wrote back. Type or paste the reply: a small offline model reads it, the text is kept outside the audit trail, and the case gets either one safe step or a request for a person to read it. The model cannot change the case, send, close or withdraw consent.",
    channel: "Reply arrived by",
    text: "Patient's reply",
    button: "Record reply",
    needText: "Enter the patient's reply first.",
    readAs: "Read as",
    quote: "Reply:",
    needsPerson: "A person must look at this reply: the case is flagged for human review and the system took no other step.",
    handled: "No person was needed for this one; the log shows the one safe step the system took, if any.",
    recordedNeedsPerson: "Reply recorded. The triage did not act on it: a person must read it.",
    recordedHandled: (intent: string) => `Reply recorded and read as ${intent}.`,
  },
  /** A26: shown while the automatic messages run on the demo placeholder approval (`GET /health` -> `outreach.demo_approval`). */
  outreachDemoBanner: {
    fallback: "DEMO PLACEHOLDER (clinical sign-off pending, H1)",
    explanation: "The automatic patient messages on this system run on a demo approval, not on a clinical lead's signature. No clinician has approved the message texts or the rules that send them. Real patients would receive nothing until the policy is signed.",
  },
  contactUpdated: "Contact details confirmed by a person",
  /** D13: the reply triage (`system:triage`). The events carry no reply text; it is stored outside the log. */
  replyReceived: "Patient reply received",
  replyReceivedNote: "The text of the reply is not in the log.",
  replyClassified: "Reply classified by the triage",
  replyClassifiedNeedsHuman: "A person must look at this reply",
  holdsLabel: "Automatic messages held",
  holds: {
    opt_out: "opt-out: until a person reviews the patient's consent",
    wrong_number: "wrong number: until a person confirms the contact details",
  },
  closureBanner: {
    heading: "Closure proposed",
    found: "The system found the colonoscopy evidence in the record:",
    humanStep: "Nothing has been closed. The case closes only when a clinician reviews the evidence and signs off.",
    button: "Review and sign off",
    buttonHint: "Opens the sign-off panel with this evidence reference already filled in.",
    notClinician: "Only the case owner (a clinician) can sign off.",
    notOwner: "Only the case owner can sign off",
  },
  effort: {
    label: "Effort on this case",
    human: "Human actions",
    automated: "Automated actions",
    share: "Automation share",
    saved: "Estimated minutes saved",
    assumed: "assumed minutes, to source",
    summary: (human: number, automated: number) => `Human actions ${human} / Automated actions ${automated}`,
  },
  effortCard: {
    heading: "Human effort",
    intro: "How much routine work the system now does so that people act only at final decisions.",
    reduction: "Reduction in human time",
    target: "Target",
    sampleLabel: "Demo cases, assumed minutes: not a programme result",
    programme: `Programme estimate (simulation, synthetic): ${PROGRAMME_LABOUR.inReachPct}% fewer staff minutes per handled case and ${PROGRAMME_LABOUR.programmePct}% across the programme; the ${PROGRAMME_LABOUR.targetPct}% target is not met in realistic scenarios.`,
    notEnoughData: "Not enough data yet",
    noData: "No effort data yet.",
    humanActions: "Human actions",
    automatedActions: "Automated actions",
    share: "Automation share",
    minutes: "Human minutes / baseline minutes",
    saved: "Minutes saved",
    caveat: "Assumed minutes, to source: the minutes behind these figures are estimates, not measurements.",
    unavailable: "Effort metrics are not available for this role or this server.",
  },
} as const;
