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

export interface PatientStrings {
  appTitle: string;
  language: string;
  switchTo: string;
  loading: string;
  loadError: string;
  offline: string;
  resultHeading: string;
  resultBody: string;
  explainInPerson: string;
  nextHeading: string;
  nextSteps: string[];
  ownerHeading: string;
  ownerLine: (name: string, role: string) => string;
  ownerPending: string;
  /**
   * SEC-2: the API shows a patient the owner by role only, no name. One full sentence per backend role
   * (`primary_care_owner`, `specialist_owner`, `coordinator`, `supervisor`), and `other` for any role not listed.
   */
  ownerByRole: { primary_care_owner: string; specialist_owner: string; coordinator: string; supervisor: string; other: string };
  understand: string;
  understandDone: string;
  understandWait: string;
  dontUnderstand: string;
  callbackDone: string;
  consentHeading: string;
  channelLabel: string;
  channels: { phone: string; sms: string; whatsapp: string; letter: string };
  shareLabel: string;
  withdrawButton: string;
  withdrawHint: string;
  withdrawnNotice: string;
  resumeButton: string;
  saved: string;
  saveFailed: string;
  actionFailed: string;
  progressHeading: string;
  progress: string[];
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
    resultHeading: "Your result is ready",
    resultBody:
      "Your screening result is ready. Your care team needs to talk with you about it and about the next step.",
    explainInPerson: "Your care team will explain your result in person or by phone.",
    nextHeading: "What happens next",
    nextSteps: [
      "Your care team will talk with you about your result.",
      "The next step is an appointment for a camera test called a colonoscopy.",
      "You will get a date and easy steps to get ready.",
    ],
    ownerHeading: "Who is looking after you",
    ownerLine: (name, role) => `${iso(name)} (${role}) is looking after your care. You can ask them any question.`,
    ownerPending: "A care coordinator will be chosen for you soon.",
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
    dontUnderstand: "I don't understand this",
    callbackDone: "Thank you. A person from your care team will call you.",
    consentHeading: "Your choices",
    channelLabel: "How may we contact you?",
    channels: { phone: "Phone call", sms: "Text message", whatsapp: "WhatsApp", letter: "Letter" },
    shareLabel: "My care team may share my result with the specialist who will do my test.",
    withdrawButton: "Stop all contact",
    withdrawHint: "If you stop contact, your care team will not message or call you. A person will check your case.",
    withdrawnNotice: "You asked us to stop contact. We will not message or call you. A person on your care team will check your case.",
    resumeButton: "You may contact me again",
    saved: "Saved",
    saveFailed: "We could not save this. Please try again.",
    actionFailed: "That did not work. Please try again or call your care team.",
    progressHeading: "Where you are now",
    progress: ["Result received", "Care team ready", "Camera test", "Doctor review"],
    homeTitle: "Your screening result",
    homeBody: "Open the link your care team sent you to see your result. You can add this page to your home screen.",
  },
  ar: {
    appTitle: "نتيجة فحصك",
    language: "اللغة",
    switchTo: "English",
    loading: "جارٍ التحميل...",
    loadError: "لم نتمكن من تحميل معلوماتك. يرجى المحاولة مرة أخرى.",
    offline: "أنت غير متصل بالإنترنت. هذه آخر معلومات حفظناها.",
    resultHeading: "نتيجتك جاهزة",
    resultBody:
      "نتيجة فحصك جاهزة. يحتاج فريق رعايتك إلى التحدث معك عنها وعن الخطوة التالية.",
    explainInPerson: "سيشرح لك فريق رعايتك نتيجتك وجهًا لوجه أو عبر الهاتف.",
    nextHeading: "ماذا سيحدث بعد ذلك",
    nextSteps: [
      "سيتحدث معك فريق رعايتك عن نتيجتك.",
      "الخطوة التالية هي موعد لفحص بالكاميرا اسمه تنظير القولون.",
      "ستحصل على موعد وخطوات سهلة للتحضير.",
    ],
    ownerHeading: "من يتابع حالتك",
    ownerLine: (name, role) => `المسؤول عن متابعة حالتك:\n${iso(name)} (${role}).\nيمكنك طرح أي سؤال.`,
    ownerPending: "سنعيّن لك منسق رعاية قريبًا.",
    ownerByRole: {
      primary_care_owner: "طبيبك في الرعاية الأولية يتابع حالتك. يمكنك أن تطرح عليه أي سؤال.",
      specialist_owner: "طبيبك الأخصائي يتابع حالتك. يمكنك أن تطرح عليه أي سؤال.",
      coordinator: "منسق الرعاية الخاص بك يتابع حالتك. يمكنك أن تطرح عليه أي سؤال.",
      supervisor: "أحد كبار أعضاء فريق رعايتك يتابع حالتك. يمكنك أن تطرح عليه أي سؤال.",
      other: "فريق رعايتك يتابع حالتك. يمكنك أن تطرح عليه أي سؤال.",
    },
    understand: "فهمت، يرجى التواصل معي",
    understandDone: "شكرًا لك. سجلنا طلبك. سيتواصل معك فريق الرعاية.",
    understandWait: "يمكنك الضغط على هذا الزر بعد أن يرسل لك فريق الرعاية رسالة.",
    dontUnderstand: "لا أفهم هذا",
    callbackDone: "شكرًا لك. سيتصل بك أحد أفراد فريق الرعاية.",
    consentHeading: "اختياراتك",
    channelLabel: "كيف يمكننا التواصل معك؟",
    channels: { phone: "مكالمة هاتفية", sms: "رسالة نصية", whatsapp: "واتساب", letter: "رسالة مكتوبة" },
    shareLabel: "أسمح لفريق رعايتي بمشاركة نتيجتي مع الأخصائي الذي سيجري فحصي.",
    withdrawButton: "أوقفوا كل تواصل",
    withdrawHint: "إذا أوقفت التواصل، فلن يرسل لك فريق الرعاية رسائل ولن يتصل بك. سيراجع أحد الأشخاص حالتك.",
    withdrawnNotice: "طلبت إيقاف التواصل. لن نرسل لك رسائل ولن نتصل بك. سيراجع أحد أفراد فريق الرعاية حالتك.",
    resumeButton: "يمكنكم التواصل معي مرة أخرى",
    saved: "تم الحفظ",
    saveFailed: "لم نتمكن من الحفظ. يرجى المحاولة مرة أخرى.",
    actionFailed: "لم تنجح العملية. يرجى المحاولة مرة أخرى أو الاتصال بفريق الرعاية.",
    progressHeading: "أين أنت الآن",
    progress: ["وصلت النتيجة", "فريق الرعاية جاهز", "الفحص بالكاميرا", "مراجعة الطبيب"],
    homeTitle: "نتيجة فحصك",
    homeBody: "افتح الرابط الذي أرسله لك فريق الرعاية لترى نتيجتك. يمكنك إضافة هذه الصفحة إلى الشاشة الرئيسية.",
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
