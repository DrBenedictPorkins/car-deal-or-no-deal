/** Formatting lives here and nowhere else — cents stay cents until the last moment. */

export function money(cents: number | null | undefined, dash = "—"): string {
  if (cents === null || cents === undefined) return dash;
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${Math.floor(abs / 100).toLocaleString()}.${String(abs % 100).padStart(2, "0")}`;
}

export function signedMoney(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return (cents > 0 ? "+" : "") + money(cents);
}

export function percent(basisPoints: number | null | undefined): string {
  if (basisPoints === null || basisPoints === undefined) return "—";
  return `${(basisPoints / 100).toFixed(2)}%`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ago(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "—";
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

const ACRONYMS = new Set(["BDC", "OTD", "VIN", "APR", "MSRP", "SMS", "LLM"]);

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .split(/[_\s]+/)
    .map((word) =>
      ACRONYMS.has(word.toUpperCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase(),
    )
    .join(" ");
}
