import type { ReactNode } from "react";

export function Panel({
  title,
  count,
  actions,
  tight,
  children,
}: {
  title: string;
  count?: ReactNode;
  actions?: ReactNode;
  tight?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <h2>
        {title}
        {count !== undefined && <span className="count">{count}</span>}
        <span className="spacer" />
        {actions}
      </h2>
      <div className={tight ? "panel-body tight" : "panel-body"}>{children}</div>
    </section>
  );
}

export type Tone = "neutral" | "good" | "warn" | "bad" | "accent";

export function Chip({ tone = "neutral", children, title }: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`chip ${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading() {
  return <div className="empty">Loading…</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error">⚠ {message}</div>;
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>{title}</h3>
          <button className="small" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="body">{children}</div>
      </div>
    </div>
  );
}

/** State chips use the same colours everywhere so the board reads at a glance. */
export function stateTone(code: string): Tone {
  switch (code) {
    case "ACCEPTED":
      return "good";
    case "QUOTE_RECEIVED":
    case "FINALIST":
      return "accent";
    case "NO_RESPONSE":
    case "LOST":
      return "bad";
    case "AWAITING_RESPONSE":
    case "AWAITING_COUNTER":
    case "AWAITING_QUOTE":
      return "warn";
    default:
      return "neutral";
  }
}

export function severityTone(severity: string): Tone {
  if (severity === "CRITICAL") return "bad";
  if (severity === "WARNING") return "warn";
  return "neutral";
}
