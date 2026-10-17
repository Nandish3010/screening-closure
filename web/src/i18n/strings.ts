/**
 * Patient-facing strings, English and Arabic. Grade-6 reading level: short, plain sentences.
 *
 * TODO: native-speaker review - every Arabic string below was written by the engineering team,
 * not yet reviewed by a native Arabic speaker (plan, Week 3: "Arabic speaker review of all
 * patient-facing strings"). Do not ship to patients before that review. Use Modern Standard
 * Arabic that is easy to read; avoid dialect. Check gender-neutral phrasing and medical terms
 * (تنظير القولون = colonoscopy) with the reviewer.
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
  notDiagnosis: string;
  nextHeading: string;
  nextSteps: string[];
  ownerHeading: string;
  ownerLine: (name: string, role: string) => string;
  ownerPending: string;
  understand: string;
  understandDone: string;
  understandWait: string;
  dontUnderstand: string;
  callbackDone: string;
  consentHeading: string;
  channelLabel: string;
  channels: { phone: string; sms: string; whatsapp: string };
  shareLabel: string;
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
    resultHeading: "What your result means",
    resultBody:
      "Your screening test found blood. This does not mean you have cancer. The next step is a camera test called a colonoscopy.",
    notDiagnosis: "This page is not a diagnosis. Your doctor will explain your result.",
    nextHeading: "What happens next",
    nextSteps: [
      "A care coordinator will call you to book the camera test.",
      "You will get a date and easy steps to get ready.",
      "A doctor will check the test and tell you what it means.",
    ],
    ownerHeading: "Who is looking after you",
    ownerLine: (name, role) => `${iso(name)} (${role}) is looking after your care. You can ask them any question.`,
    ownerPending: "A care coordinator will be chosen for you soon.",
    understand: "I understand",
    understandDone: "Thank you. We saved that you understand.",
    understandWait: "You can tap this button after your care team has contacted you.",
    dontUnderstand: "I don't understand this",
    callbackDone: "Thank you. A person from your care team will call you.",
    consentHeading: "Your choices",
    channelLabel: "How should we contact you?",
    channels: { phone: "Phone call", sms: "Text message", whatsapp: "WhatsApp" },
    shareLabel: "My care team may share my result with the specialist who will do my test.",
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
    resultHeading: "ماذا تعني نتيجتك",
    resultBody:
      "أظهر فحصك وجود دم. هذا لا يعني أنك مصاب بالسرطان. الخطوة التالية هي فحص بالكاميرا اسمه تنظير القولون.",
    notDiagnosis: "هذه الصفحة ليست تشخيصًا. سيشرح لك طبيبك نتيجتك.",
    nextHeading: "ماذا سيحدث بعد ذلك",
    nextSteps: [
      "سيتصل بك منسق الرعاية لحجز الفحص بالكاميرا.",
      "ستحصل على موعد وخطوات سهلة للتحضير.",
      "سيراجع الطبيب الفحص ويشرح لك معناه.",
    ],
    ownerHeading: "من يتابع حالتك",
    ownerLine: (name, role) => `المسؤول عن متابعة حالتك:\n${iso(name)} (${role}).\nيمكنك طرح أي سؤال.`,
    ownerPending: "سنعيّن لك منسق رعاية قريبًا.",
    understand: "فهمت",
    understandDone: "شكرًا لك. سجلنا أنك فهمت.",
    understandWait: "يمكنك الضغط على هذا الزر بعد أن يتواصل معك فريق الرعاية.",
    dontUnderstand: "لا أفهم هذا",
    callbackDone: "شكرًا لك. سيتصل بك أحد أفراد فريق الرعاية.",
    consentHeading: "اختياراتك",
    channelLabel: "كيف نتواصل معك؟",
    channels: { phone: "مكالمة هاتفية", sms: "رسالة نصية", whatsapp: "واتساب" },
    shareLabel: "أسمح لفريق رعايتي بمشاركة نتيجتي مع الأخصائي الذي سيجري فحصي.",
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
