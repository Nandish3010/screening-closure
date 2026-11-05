/** The product mark: a ring closed by a tick (a loop that is closed). Decorative; the words next to it carry the name. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray="70 12" strokeLinecap="round" transform="rotate(-60 16 16)" />
      <path d="M10.5 16.5l4 4 7-8.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** "Screening Closure" with the small "by Humans Decide" line under it: the header and the sign-in page. */
export function BrandLockup({ large = false }: { large?: boolean }) {
  return (
    <span className={`brand-lockup${large ? " brand-lockup-lg" : ""}`}>
      <BrandMark size={large ? 44 : 28} />
      <span className="brand-words">
        <span className="brand-name">Screening Closure</span>
        <span className="brand-by">by Humans Decide</span>
      </span>
    </span>
  );
}
