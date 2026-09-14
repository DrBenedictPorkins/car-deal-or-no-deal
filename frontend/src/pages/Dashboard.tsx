import { useState } from "react";
import { Link } from "react-router-dom";

import { api, useQuery } from "../api";
import { Chip, ErrorBox, Loading, Panel, severityTone, stateTone } from "../components/ui";
import { ago, money, titleCase, when } from "../format";
import type { Answer, Dashboard as DashboardData, DashboardRow, Notification } from "../types";

const QUESTIONS: { key: string; label: string }[] = [
  { key: "best_offer", label: "What is our best offer?" },
  { key: "owes_me", label: "Who owes me a response?" },
  { key: "no_response", label: "Who hasn't responded?" },
  { key: "written_otd", label: "Who gave a written OTD?" },
  { key: "lowest_selling_price", label: "Lowest selling price?" },
  { key: "phone_pressure", label: "Who wants me on the phone?" },
  { key: "demo_confirmations", label: "Who confirmed it isn't a demo?" },
];

export default function Dashboard() {
  const dashboard = useQuery<DashboardData>("/dashboard");
  const notifications = useQuery<Notification[]>("/notifications");
  const [refreshing, setRefreshing] = useState(false);
  const [asked, setAsked] = useState<Answer | null>(null);

  async function refresh() {
    setRefreshing(true);
    try {
      await api.post("/system/refresh");
      dashboard.reload();
      notifications.reload();
    } finally {
      setRefreshing(false);
    }
  }

  if (dashboard.error) return <ErrorBox message={dashboard.error} />;
  if (!dashboard.data) return <Loading />;

  const { summary, rows } = dashboard.data;

  return (
    <>
      <section className="panel">
        <div className="stats">
          <div className="stat">
            <div className="label">Best out-the-door</div>
            <div className="value money">{money(summary.best_otd_cents)}</div>
            <div className="sub">{summary.best_otd_dealer ?? "no priced offer yet"}</div>
          </div>
          <div className="stat">
            <div className="label">Best dealer-controlled</div>
            <div className="value money">{money(summary.best_dealer_controlled_cents)}</div>
            <div className="sub">
              {summary.best_dealer_controlled_dealer ?? "—"} · tax &amp; registration excluded
            </div>
          </div>
          <div className="stat">
            <div className="label">Contacted</div>
            <div className="value">{summary.dealers_contacted} / {summary.dealers_total}</div>
            <div className="sub">
              {summary.dealers_responded} replied · {summary.dealers_silent} silent
            </div>
          </div>
          <div className="stat">
            <div className="label">You owe</div>
            <div className="value">{summary.you_owe_count}</div>
            <div className="sub">dealers owe you {summary.dealer_owes_count}</div>
          </div>
          <div className="stat">
            <div className="label">Priced offers</div>
            <div className="value">{summary.dealers_with_offer}</div>
            <div className="sub">
              {summary.open_contradictions} contradiction
              {summary.open_contradictions === 1 ? "" : "s"}
            </div>
          </div>
        </div>
      </section>

      <Panel
        title="Where you stand"
        count={`${rows.length} dealers, most urgent first`}
        tight
        actions={
          <button className="small" onClick={refresh} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Dealer</th>
                <th>Contact</th>
                <th className="num">Selling</th>
                <th className="num">Dealer cost</th>
                <th className="num">OTD</th>
                <th>State</th>
                <th>Last contact</th>
                <th>Owes</th>
                <th>Next action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row key={row.dealer_id} row={row} />
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="empty">
                    No dealers yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid two">
        <Panel title="Ask" count="answered from the database, not a model">
          <div className="pill-row" style={{ marginBottom: 10 }}>
            {QUESTIONS.map((q) => (
              <button
                key={q.key}
                className="small"
                onClick={() => api.get<Answer>(`/ask/${q.key}`).then(setAsked)}
              >
                {q.label}
              </button>
            ))}
            <button
              className="small"
              onClick={() => api.get<Answer>("/ask/changed/24").then(setAsked)}
            >
              What changed in 24h?
            </button>
          </div>
          {asked ? (
            <>
              <div className="muted small">{asked.question}</div>
              <div style={{ fontWeight: 600, margin: "3px 0 8px" }}>{asked.answer}</div>
              {asked.rows.length > 0 && <AnswerTable rows={asked.rows} />}
            </>
          ) : (
            <div className="muted small">Pick a question.</div>
          )}
        </Panel>

        <Panel title="Recent activity" count={summary.recent_changes.length} tight>
          {summary.recent_changes.length === 0 ? (
            <div className="empty">Nothing in the last 24 hours.</div>
          ) : (
            <ul className="timeline">
              {summary.recent_changes.map((change, i) => (
                <li key={i} className="small">
                  {change}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Notifications" count={notifications.data?.length ?? 0} tight>
        {notifications.data && notifications.data.length > 0 ? (
          <ul className="timeline">
            {notifications.data.slice(0, 12).map((n) => (
              <li key={n.id}>
                <div className="head">
                  <Chip tone={severityTone(n.severity)}>{titleCase(n.type)}</Chip>
                  <span className="subject">{n.title}</span>
                  <span className="spacer" />
                  <button
                    className="small"
                    onClick={() =>
                      api.post(`/notifications/${n.id}/dismiss`).then(notifications.reload)
                    }
                  >
                    Dismiss
                  </button>
                </div>
                {n.body && <p className="preview">{n.body}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty">Nothing needs your attention.</div>
        )}
      </Panel>
    </>
  );
}

function Row({ row }: { row: DashboardRow }) {
  return (
    <tr>
      <td>
        <div className="dealer-cell">
          <Link to={`/dealers/${row.dealer_id}`}>{row.dealer_name}</Link>
          {row.is_local && (
            <>
              {" "}
              <Chip tone="accent">local</Chip>
            </>
          )}
          <div className="meta">
            {row.vehicle_summary ?? "no vehicle identified"}
            {row.distance_miles !== null && ` · ${row.distance_miles} mi`}
          </div>
        </div>
      </td>
      <td className="dim">
        {row.primary_contact ?? "—"}
        {row.contact_is_automated && (
          <>
            {" "}
            <Chip tone="warn" title="Messages from this contact look machine-generated.">
              auto
            </Chip>
          </>
        )}
        <div className="small">{titleCase(row.primary_contact_role)}</div>
      </td>
      <td className="num">{money(row.selling_price_cents)}</td>
      <td className="num">{money(row.dealer_controlled_cents)}</td>
      <td className="num">
        {money(row.otd_cents)}
        <div className="small">
          {row.otd_cents === null ? (
            <span className="muted">not quoted</span>
          ) : row.is_best_otd ? (
            <Chip tone="good">best</Chip>
          ) : row.gap_to_best_cents ? (
            <span className="muted">+{money(row.gap_to_best_cents)}</span>
          ) : null}
        </div>
      </td>
      <td>
        <Chip tone={stateTone(row.state_code)}>{row.state_label}</Chip>
        {row.state_is_pinned && (
          <div className="small muted" title="Set by you; the rules engine won't move it.">
            pinned
          </div>
        )}
      </td>
      <td className="dim nowrap">
        {ago(row.idle_hours)}
        <div className="small">
          {row.last_interaction_at ? (
            <>
              {titleCase(row.last_interaction_channel)} ·{" "}
              {row.last_interaction_direction === "INBOUND" ? "in" : "out"}
            </>
          ) : (
            "never"
          )}
        </div>
      </td>
      <td>
        {row.owes_response === "DEALER" ? (
          <Chip tone="warn" title={row.owes_reason}>
            dealer
          </Chip>
        ) : row.owes_response === "BUYER" ? (
          <Chip tone="accent" title={row.owes_reason}>
            you
          </Chip>
        ) : (
          <Chip title={row.owes_reason}>—</Chip>
        )}
      </td>
      <td>
        <div style={{ fontWeight: 600 }}>{row.next_action}</div>
        <div className="small muted">{row.next_action_detail}</div>
        {(row.warnings.length > 0 || row.friction_points > 0) && (
          <div className="pill-row" style={{ marginTop: 4 }}>
            {row.warnings.map((w, i) => (
              <Chip key={i} tone="bad" title={w}>
                ⚠ {w.length > 44 ? `${w.slice(0, 44)}…` : w}
              </Chip>
            ))}
            {row.friction_points > 0 && (
              <Chip tone="warn" title={row.friction_reasons.join("\n")}>
                {row.friction_points} friction
              </Chip>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function AnswerTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} className={c.endsWith("_cents") ? "num" : undefined}>
                {titleCase(c.replace(/_cents$/, ""))}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const value = row[c];
                if (c.endsWith("_cents")) {
                  return (
                    <td key={c} className="num">
                      {money(value as number | null)}
                    </td>
                  );
                }
                if (c.endsWith("_at")) return <td key={c}>{when(value as string)}</td>;
                return (
                  <td key={c}>
                    {value === null || value === undefined
                      ? "—"
                      : typeof value === "boolean"
                        ? value
                          ? "yes"
                          : "no"
                        : String(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
