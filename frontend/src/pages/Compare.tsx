import { useState } from "react";
import { Link } from "react-router-dom";

import { useQuery } from "../api";
import { Chip, ErrorBox, Loading, Panel } from "../components/ui";
import { money, percent, titleCase, when } from "../format";
import type { Comparison, ComparisonRow } from "../types";

/** Rows are metrics, columns are dealers — the shape you actually read side by side. */
const METRICS: {
  label: string;
  render: (r: ComparisonRow) => React.ReactNode;
  emphasis?: boolean;
  best?: "min" | "max";
  value?: (r: ComparisonRow) => number | null;
  /** Prose and lists are left-aligned and wrap; numbers stay right-aligned. */
  wrap?: boolean;
}[] = [
  { label: "Vehicle", render: (r) => r.vehicle_summary ?? "—" },
  { label: "VIN", render: (r) => <span className="mono small">{r.vin ?? "—"}</span> },
  { label: "Colour", render: (r) => r.exterior_color ?? "—" },
  { label: "Mileage", render: (r) => (r.mileage === null ? "—" : `${r.mileage}`) },
  {
    label: "Demo",
    render: (r) =>
      r.is_demo === null ? <span className="muted">never asked</span> : r.is_demo ? "yes" : "no",
  },
  { label: "MSRP", render: (r) => money(r.msrp_cents) },
  {
    label: "Selling price",
    render: (r) => money(r.selling_price_cents),
    value: (r) => r.selling_price_cents,
    best: "min",
  },
  { label: "Discount from MSRP", render: (r) => money(r.discount_from_msrp_cents) },
  {
    label: "Dealer fees",
    render: (r) =>
      r.fees_disclosed ? (
        money(r.dealer_fees_total_cents)
      ) : (
        <span className="muted">not disclosed</span>
      ),
  },
  { label: "Add-ons", render: (r) => money(r.add_ons_total_cents) },
  {
    label: "Dealer-controlled cost",
    render: (r) => money(r.dealer_controlled_cents),
    value: (r) => r.dealer_controlled_cents,
    best: "min",
    emphasis: true,
  },
  { label: "Without unwanted add-ons", render: (r) => money(r.clean_dealer_controlled_cents) },
  { label: "Tax", render: (r) => money(r.tax_cents) },
  { label: "Registration", render: (r) => money(r.registration_cents) },
  { label: "Government total", render: (r) => money(r.government_total_cents) },
  {
    label: "Out the door",
    render: (r) => money(r.otd_cents),
    value: (r) => r.otd_cents,
    best: "min",
    emphasis: true,
  },
  {
    label: "Unexplained in quote",
    render: (r) =>
      r.otd_variance_cents ? (
        <Chip tone="bad">{money(Math.abs(r.otd_variance_cents))}</Chip>
      ) : (
        <span className="muted">none</span>
      ),
  },
  {
    label: "Financing required",
    render: (r) =>
      r.financing_required === null ? (
        <span className="muted">—</span>
      ) : r.financing_required ? (
        <Chip tone="warn">
          {r.financing_provider ?? "yes"}
          {r.apr_bp !== null && ` · ${percent(r.apr_bp)}`}
          {r.financing_term_months !== null && ` · ${r.financing_term_months}mo`}
        </Chip>
      ) : (
        <Chip tone="good">no</Chip>
      ),
  },
  {
    label: "Distance",
    render: (r) => (r.distance_miles === null ? "—" : `${r.distance_miles} mi`),
  },
  {
    label: "Friction",
    render: (r) => (
      <Chip
        tone={r.friction_points > 2 ? "bad" : r.friction_points > 0 ? "warn" : "good"}
        title={r.friction_reasons.join("\n")}
      >
        {r.friction_points || "none"}
      </Chip>
    ),
  },
  {
    label: "Cleanliness",
    render: (r) => (
      <Chip
        tone={r.cleanliness_score >= 80 ? "good" : r.cleanliness_score >= 50 ? "warn" : "bad"}
        title={r.cleanliness_reasons.join("\n")}
      >
        {r.cleanliness_score}/100
      </Chip>
    ),
  },
  { label: "Quoted", render: (r) => <span className="small">{when(r.quoted_at)}</span> },
  {
    label: "Unresolved",
    wrap: true,
    render: (r) =>
      r.unresolved_issues.length === 0 ? (
        <span className="muted">none</span>
      ) : (
        <ul className="reasons" style={{ paddingLeft: 14 }}>
          {r.unresolved_issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      ),
  },
];

export default function Compare() {
  const comparison = useQuery<Comparison>("/compare");
  const [selected, setSelected] = useState<number[] | null>(null);

  if (comparison.error) return <ErrorBox message={comparison.error} />;
  if (!comparison.data) return <Loading />;

  const all = comparison.data.rows;
  const rows = selected === null ? all : all.filter((r) => selected.includes(r.dealer_id));

  function toggle(id: number) {
    setSelected((current) => {
      const base = current ?? all.map((r) => r.dealer_id);
      const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
      return next.length === all.length ? null : next;
    });
  }

  return (
    <>
      <Panel title="Winners" count="four separate axes, deliberately not one score" tight>
        <div className="winners">
          {comparison.data.winners.map((w) => (
            <div className="winner" key={w.label}>
              <div className="label">{w.label}</div>
              <div className="name">
                {w.dealer_id ? (
                  <Link to={`/dealers/${w.dealer_id}`}>{w.dealer_name}</Link>
                ) : (
                  <span className="muted">—</span>
                )}
              </div>
              <div className="value">{w.value}</div>
              <div className="why">{w.explanation}</div>
            </div>
          ))}
        </div>
      </Panel>

      {comparison.data.notes.length > 0 && (
        <Panel title="Caveats">
          <ul className="reasons">
            {comparison.data.notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        title="Side by side"
        count={`${rows.length} of ${all.length} dealers`}
        actions={
          <div className="pill-row">
            {all.map((r) => (
              <button
                key={r.dealer_id}
                className="small"
                style={{
                  opacity: selected === null || selected.includes(r.dealer_id) ? 1 : 0.45,
                }}
                onClick={() => toggle(r.dealer_id)}
              >
                {r.dealer_name}
              </button>
            ))}
          </div>
        }
        tight
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ minWidth: 190 }}>Metric</th>
                {rows.map((r) => (
                  <th key={r.dealer_id} className="num">
                    <Link to={`/dealers/${r.dealer_id}`}>{r.dealer_name}</Link>
                    <div className="small muted" style={{ fontWeight: 400 }}>
                      {titleCase(r.state_code)}
                      {r.is_local && " · local"}
                      {r.offer_version !== null && ` · v${r.offer_version}`}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {METRICS.map((metric) => {
                const values = metric.value
                  ? rows.map(metric.value).filter((v): v is number => v !== null)
                  : [];
                const best =
                  metric.best === "min" && values.length > 1 ? Math.min(...values) : null;
                return (
                  <tr key={metric.label}>
                    <td style={{ fontWeight: metric.emphasis ? 700 : 400 }}>{metric.label}</td>
                    {rows.map((r) => {
                      const isBest =
                        best !== null && metric.value && metric.value(r) === best;
                      return (
                        <td
                          key={r.dealer_id}
                          className={metric.wrap ? "" : "num"}
                          style={{
                            fontWeight: metric.emphasis || isBest ? 700 : 400,
                            color: isBest ? "var(--good)" : undefined,
                          }}
                        >
                          {metric.render(r)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
