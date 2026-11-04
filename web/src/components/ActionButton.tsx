import { useId, type ReactNode } from "react";

/** A button that, when disabled, always says why (visible text, linked with aria-describedby). */
export function ActionButton({
  children, onClick, disabledReason, busy, primary, testId, type = "button", quietWhy,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabledReason?: string | null;
  busy?: boolean;
  primary?: boolean;
  testId?: string;
  type?: "button" | "submit";
  /** Keep the reason for assistive technology only (the next action is obvious, e.g. an empty field after a recorded reply). */
  quietWhy?: boolean;
}) {
  const id = useId();
  const disabled = Boolean(disabledReason) || Boolean(busy);
  return (
    <div className="action">
      <button
        type={type}
        className={`btn ${primary ? "btn-primary" : ""}`}
        onClick={onClick}
        disabled={disabled}
        aria-describedby={disabledReason ? id : undefined}
        data-testid={testId}
      >
        {children}
      </button>
      {disabledReason && <p id={id} className={quietWhy ? "visually-hidden" : "why"} data-testid={testId ? `${testId}-why` : undefined}>{disabledReason}</p>}
    </div>
  );
}
