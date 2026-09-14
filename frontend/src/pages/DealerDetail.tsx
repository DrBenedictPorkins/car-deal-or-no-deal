import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, useQuery } from "../api";
import {
  Chip,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  severityTone,
  stateTone,
} from "../components/ui";
import { ago, money, percent, signedMoney, titleCase, when } from "../format";
import type {
  DealerDetail,
  Draft,
  Fact,
  Offer,
  Provenance,
  ScoredDimension,
  StateDef,
} from "../types";

export default function DealerDetailPage() {
  const { id } = useParams();
  const detail = useQuery<DealerDetail>(`/dealers/${id}/detail`, [id]);
  const states = useQuery<StateDef[]>("/states");
  const [provenanceOf, setProvenanceOf] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  if (detail.error) return <ErrorBox message={detail.error} />;
  if (!detail.data) return <Loading />;
  const d = detail.data;

  async function setState(code: string) {
    await api.put(`/dealers/${id}/state`, { state_code: code, pin: true });
    detail.reload();
  }

  async function generateDraft() {
    setBusy(true);
    try {
      await api.post(`/dealers/${id}/drafts/generate`);
      detail.reload();
    } finally {
      setBusy(false);
    }
  }

  const severity =
    d.recommendation.severity === "CRITICAL"
      ? "bad"
      : d.recommendation.severity === "WARNING"
        ? "warn"
        : "";

  return (
    <>
      <div className="pill-row" style={{ marginBottom: 12 }}>
        <Link to="/">← Dashboard</Link>
        <span className="spacer" />
      </div>

      <section className="panel">
        <div className="panel-body">
          <div className="pill-row" style={{ marginBottom: 6 }}>
            <h1 style={{ margin: 0, fontSize: 19 }}>{d.dealer.name}</h1>
            <Chip tone={stateTone(d.dealer.state_code)}>{d.state_label}</Chip>
            {d.dealer.state_is_pinned && <Chip>pinned</Chip>}
            {d.dealer.is_local && <Chip tone="accent">local</Chip>}
            <span className="spacer" />
            <select
              value={d.dealer.state_code}
              onChange={(e) => setState(e.target.value)}
              style={{ width: 190 }}
            >
              {(states.data ?? []).map((s) => (
                <option key={s.code} value={s.code}>
                  {s.label}
                </option>
              ))}
            </select>
            {d.dealer.state_is_pinned && (
              <button
                className="small"
                onClick={() =>
                  api.post(`/dealers/${id}/state/unpin`).then(() => detail.reload())
                }
                title="Hand this dealer back to the rules engine."
              >
                Unpin
              </button>
            )}
          </div>
          <p className="muted" style={{ margin: "0 0 10px" }}>
            {[d.dealer.city, d.dealer.state].filter(Boolean).join(", ")}
            {d.dealer.distance_miles !== null && ` · ${d.dealer.distance_miles} mi`}
            {" · "}
            {d.owes_response === "DEALER"
              ? "dealer owes the next response"
              : d.owes_response === "BUYER"
                ? "you owe the next response"
                : "nobody owes a response"}{" "}
            ({d.owes_reason.toLowerCase().replace(/\.$/, "")}) · last contact {ago(d.idle_hours)}
          </p>
          <p style={{ margin: 0 }}>{d.summary}</p>
        </div>
      </section>

      <div className={`panel`}>
        <div className="panel-body">
          <div className={`callout ${severity}`}>
            <h3>{d.recommendation.headline}</h3>
            <p>{d.recommendation.detail}</p>
            {Object.keys(d.recommendation.supporting_numbers).length > 0 && (
              <dl className="kv" style={{ marginTop: 8 }}>
                {Object.entries(d.recommendation.supporting_numbers).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </div>
      </div>

      {d.contradictions.length > 0 && (
        <Panel title="Contradictions" count={d.contradictions.length}>
          {d.contradictions.map((c) => (
            <div key={c.id} className="callout bad" style={{ marginBottom: 8 }}>
              <h3>
                <Chip tone={severityTone(c.severity)}>{titleCase(c.kind)}</Chip> {c.summary}
              </h3>
              <p className="small">{c.detail_a}</p>
              <p className="small">{c.detail_b}</p>
              <p className="small muted" style={{ marginTop: 6 }}>
                Both statements are shown as recorded. Nothing here decides which is correct.
              </p>
            </div>
          ))}
        </Panel>
      )}

      <div className="grid two">
        <div>
          <Panel
            title="Current offer"
            count={d.current_offer ? `v${d.current_offer.version}` : undefined}
          >
            {d.current_offer ? (
              <OfferBreakdown offer={d.current_offer} />
            ) : (
              <div className="muted">No priced offer from this dealer.</div>
            )}
          </Panel>

          {d.offer_history.length > 1 && (
            <Panel title="Offer history" count={`${d.offer_history.length} versions`} tight>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Version</th>
                      <th>Quoted</th>
                      <th className="num">Selling</th>
                      <th className="num">Dealer cost</th>
                      <th className="num">OTD</th>
                      <th className="num">Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.offer_history.map((offer, i) => {
                      const delta = d.offer_progression.find(
                        (p) => p.to_version === offer.version,
                      );
                      return (
                        <tr key={offer.id}>
                          <td>v{offer.version}{i === d.offer_history.length - 1 && " (current)"}</td>
                          <td className="dim nowrap">{when(offer.quoted_at)}</td>
                          <td className="num">{money(offer.pricing?.selling_price_cents)}</td>
                          <td className="num">{money(offer.pricing?.dealer_controlled_cents)}</td>
                          <td className="num">{money(offer.pricing?.effective_otd_cents)}</td>
                          <td className="num">
                            {delta ? (
                              <span className={delta.improved ? "" : "muted"}>
                                {signedMoney(delta.otd_delta_cents)}
                              </span>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {d.offer_progression.some(
                (p) => p.added_lines.length > 0 || p.removed_lines.length > 0,
              ) && (
                <div className="panel-body small muted">
                  {d.offer_progression.map((p) => (
                    <div key={`${p.from_version}-${p.to_version}`}>
                      v{p.from_version} → v{p.to_version}:
                      {p.added_lines.length > 0 && ` added ${p.added_lines.join(", ")}`}
                      {p.removed_lines.length > 0 && ` removed ${p.removed_lines.join(", ")}`}
                      {p.added_lines.length === 0 && p.removed_lines.length === 0 && " price only"}
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          )}

          <Panel title="Timeline" count={`${d.timeline.length} events`} tight>
            <ul className="timeline">
              {d.timeline.map((event) => (
                <li
                  key={`${event.kind}-${event.id}`}
                  className={
                    event.kind === "OFFER"
                      ? "offer"
                      : event.direction === "OUTBOUND"
                        ? "outbound"
                        : "inbound"
                  }
                >
                  <div className="head">
                    <Chip tone={event.kind === "OFFER" ? "warn" : "neutral"}>
                      {event.kind === "OFFER" ? "OFFER" : titleCase(event.channel)}
                    </Chip>
                    {event.kind !== "OFFER" && (
                      <Chip tone={event.direction === "INBOUND" ? "good" : "accent"}>
                        {event.direction === "INBOUND" ? "from dealer" : "from you"}
                      </Chip>
                    )}
                    {event.actor_kind === "AUTOMATED" && <Chip tone="warn">automated</Chip>}
                    <span className="subject">{event.subject}</span>
                    <span className="spacer" />
                    <time>{when(event.at)}</time>
                  </div>
                  {event.kind === "OFFER" ? (
                    <p className="preview">
                      {money(event.otd_cents)} out the door ·{" "}
                      {money(event.dealer_controlled_cents)} dealer-controlled
                      {event.warnings && event.warnings.length > 0 && ` · ⚠ ${event.warnings[0]}`}
                    </p>
                  ) : (
                    event.preview && <p className="preview">{event.preview}</p>
                  )}
                </li>
              ))}
              {d.timeline.length === 0 && <li className="empty">No interactions recorded.</li>}
            </ul>
          </Panel>
        </div>

        <div>
          <Panel title="Contacts" count={d.contacts.length}>
            {d.contacts.map((c) => (
              <div key={c.id} style={{ marginBottom: 10 }}>
                <div className="pill-row">
                  <strong>{c.name ?? "Unknown"}</strong>
                  {c.is_primary && <Chip tone="accent">primary</Chip>}
                  {c.actor_kind === "AUTOMATED" && <Chip tone="warn">automated</Chip>}
                  {c.actor_kind === "HUMAN" && <Chip tone="good">human</Chip>}
                </div>
                <div className="small muted">
                  {Array.from(
                    new Set([c.title, titleCase(c.role), c.email].filter(Boolean) as string[]),
                  ).join(" · ")}
                </div>
                {c.automation_evidence && (
                  <div className="quote small">{c.automation_evidence}</div>
                )}
              </div>
            ))}
            {d.contacts.length === 0 && <div className="muted">No contacts recorded.</div>}
          </Panel>

          <Panel title="Vehicles" count={d.vehicles.length}>
            {d.vehicles.map((v) => (
              <dl className="kv" key={v.id} style={{ marginBottom: 10 }}>
                <dt>Vehicle</dt>
                <dd className="text">
                  {[v.year, v.make, v.model, v.trim].filter(Boolean).join(" ")}
                </dd>
                <dt>VIN</dt>
                <dd>{v.vin ?? "—"}</dd>
                <dt>Colour</dt>
                <dd className="text">{v.exterior_color ?? "—"}</dd>
                <dt>Mileage</dt>
                <dd>{v.mileage ?? "—"}</dd>
                <dt>MSRP</dt>
                <dd>{money(v.msrp_cents)}</dd>
                <dt>Demo</dt>
                <dd className="text">
                  {v.is_demo === null ? (
                    <span className="muted">never asked</span>
                  ) : v.is_demo ? (
                    "yes"
                  ) : (
                    "confirmed no"
                  )}
                </dd>
              </dl>
            ))}
            {d.vehicles.length === 0 && <div className="muted">No vehicle identified.</div>}
          </Panel>

          <Panel title="Behavior" count="every score shows its reasons">
            <Dimension dimension={d.behavior.transparency} />
            <Dimension dimension={d.behavior.responsiveness} />
            <Dimension dimension={d.behavior.price_competitiveness} />
            <div style={{ marginTop: 10 }}>
              <div className="pill-row">
                <strong>Friction</strong>
                <Chip tone={d.behavior.friction_points > 2 ? "bad" : "warn"}>
                  {d.behavior.friction_label} ({d.behavior.friction_points})
                </Chip>
              </div>
              <ul className="reasons">
                {d.behavior.friction_reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
                {d.behavior.friction_reasons.length === 0 && <li>No friction recorded.</li>}
              </ul>
            </div>
          </Panel>

          <Panel title="Open questions" count={d.questions.filter((q) => q.status === "OPEN").length}>
            {d.questions.length === 0 && <div className="muted">None recorded.</div>}
            {d.questions.map((q) => (
              <div key={q.id} style={{ marginBottom: 7 }}>
                <div className="pill-row">
                  <Chip tone={q.status === "OPEN" ? "warn" : "good"}>{titleCase(q.status)}</Chip>
                  <span className="small muted">
                    asked by {q.asked_by === "BUYER" ? "you" : "dealer"} · {when(q.asked_at)}
                  </span>
                </div>
                <div>{q.text}</div>
              </div>
            ))}
          </Panel>

          <Panel title="Commitments" count={d.commitments.length}>
            {d.commitments.length === 0 && <div className="muted">None recorded.</div>}
            {d.commitments.map((c) => (
              <div key={c.id} style={{ marginBottom: 7 }}>
                <div className="pill-row">
                  <Chip tone={c.status === "KEPT" ? "good" : c.status === "OPEN" ? "warn" : "bad"}>
                    {titleCase(c.status)}
                  </Chip>
                  <span className="small muted">
                    {c.party === "DEALER" ? "dealer" : "you"} · due {when(c.due_at)}
                  </span>
                </div>
                <div>{c.text}</div>
              </div>
            ))}
          </Panel>

          <Panel title="Facts" count={`${d.facts.length} · click for the source`}>
            {d.facts.length === 0 && <div className="muted">No facts recorded yet.</div>}
            {d.facts.map((f) => (
              <FactRow key={f.id} fact={f} onOpen={() => setProvenanceOf(f.id)} />
            ))}
          </Panel>

          <Panel
            title="Drafts"
            count={d.drafts.length}
            actions={
              <button className="small" onClick={generateDraft} disabled={busy}>
                {busy ? "Generating…" : "Generate"}
              </button>
            }
          >
            {d.drafts.length === 0 && (
              <div className="muted">
                No drafts. Generating one uses the whole negotiation state, not just the
                last email. Nothing is ever sent automatically.
              </div>
            )}
            {d.drafts.map((draft) => (
              <DraftCard key={draft.id} draft={draft} onChange={detail.reload} />
            ))}
          </Panel>

          <Panel title="State history" count={d.transitions.length} tight>
            <ul className="timeline">
              {d.transitions.map((t) => (
                <li key={t.id} className="small">
                  <div className="head">
                    <span className="subject">
                      {t.from_state ?? "—"} → {t.to_state}
                    </span>
                    <span className="spacer" />
                    <time>{when(t.created_at)}</time>
                  </div>
                  <p className="preview">
                    {t.reason_text}
                    {t.was_suppressed_by_pin && " (suppressed — state is pinned)"}
                  </p>
                </li>
              ))}
              {d.transitions.length === 0 && <li className="empty">No changes yet.</li>}
            </ul>
          </Panel>
        </div>
      </div>

      {provenanceOf !== null && (
        <ProvenanceModal factId={provenanceOf} onClose={() => setProvenanceOf(null)} />
      )}
    </>
  );
}

function Dimension({ dimension }: { dimension: ScoredDimension }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="pill-row">
        <strong>{titleCase(dimension.dimension)}</strong>
        <Chip
          tone={
            dimension.score === null
              ? "neutral"
              : dimension.score >= 70
                ? "good"
                : dimension.score >= 45
                  ? "warn"
                  : "bad"
          }
        >
          {dimension.score === null ? "not enough data" : `${dimension.score}/100`}
        </Chip>
      </div>
      <ul className="reasons">
        {dimension.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

function OfferBreakdown({ offer }: { offer: Offer }) {
  const p = offer.pricing;
  if (!p) return <div className="muted">No pricing computed.</div>;
  return (
    <>
      <dl className="kv">
        <dt>MSRP</dt>
        <dd>{money(p.msrp_cents)}</dd>
        {p.price_basis === "ADVERTISED" ? (
          <>
            <dt>Advertised price</dt>
            <dd>
              {money(offer.advertised_price_cents)}
              <span className="muted">
                {offer.advertised_includes_fees ? " (includes fees)" : " (no quote given)"}
              </span>
            </dd>
          </>
        ) : (
          <>
            <dt>Selling price</dt>
            <dd>{money(p.selling_price_cents)}</dd>
          </>
        )}
        {p.discount_from_msrp_cents !== null && (
          <>
            <dt>Discount from MSRP</dt>
            <dd>{money(p.discount_from_msrp_cents)}</dd>
          </>
        )}
        <dt>Dealer fees</dt>
        <dd>
          {p.fees_disclosed ? (
            money(p.dealer_fees_total_cents)
          ) : (
            <span className="muted">not disclosed</span>
          )}
        </dd>
        <dt>Add-ons</dt>
        <dd>{money(p.add_ons_total_cents)}</dd>
        <dt>
          <strong>Dealer-controlled</strong>
        </dt>
        <dd>
          <strong>{money(p.dealer_controlled_cents)}</strong>
        </dd>
        <dt>Tax, title &amp; registration</dt>
        <dd>
          {p.government_disclosed ? (
            money(p.government_total_cents)
          ) : (
            <span className="muted">not disclosed</span>
          )}
        </dd>
        <dt>
          <strong>Out the door</strong>
        </dt>
        <dd>
          <strong>{money(p.effective_otd_cents)}</strong>
          {p.quoted_otd_cents === null && <span className="muted"> (derived)</span>}
        </dd>
        {p.unwanted_add_ons_cents > 0 && (
          <>
            <dt>Without unwanted add-ons</dt>
            <dd>{money(p.clean_otd_cents)}</dd>
          </>
        )}
        {p.implied_tax_rate_bp !== null && (
          <>
            <dt>Implied tax rate</dt>
            <dd>
              {percent(p.implied_tax_rate_bp)}
              {p.expected_tax_rate_bp !== null && (
                <span className="muted"> (expected {percent(p.expected_tax_rate_bp)})</span>
              )}
            </dd>
          </>
        )}
      </dl>

      {offer.lines.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead>
              <tr>
                <th>Line</th>
                <th className="num">Price</th>
                <th>Claimed</th>
                <th>Wanted</th>
              </tr>
            </thead>
            <tbody>
              {offer.lines.map((line) => (
                <tr key={line.id}>
                  <td>
                    {line.name}
                    <div className="small muted">{titleCase(line.kind)}</div>
                  </td>
                  <td className="num">{money(line.price_cents)}</td>
                  <td className="small">
                    {line.mandatory_claimed ? "mandatory" : "optional"}
                    {line.already_installed && " · already installed"}
                    {line.removable_confirmed === false && " · not removable"}
                  </td>
                  <td>
                    {line.user_wants ? (
                      <Chip tone="good">yes</Chip>
                    ) : (
                      <Chip tone="bad">no</Chip>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {offer.financing_required && (
        <div className="callout warn" style={{ marginTop: 10 }}>
          <h3>This price requires dealer financing</h3>
          <dl className="kv">
            <dt>Lender</dt>
            <dd className="text">{offer.financing_provider ?? "unspecified"}</dd>
            <dt>APR</dt>
            <dd>{percent(offer.apr_bp)}</dd>
            <dt>Term</dt>
            <dd>{offer.financing_term_months ?? "—"} months</dd>
            <dt>Prepayment penalty</dt>
            <dd className="text">
              {offer.prepayment_penalty === null ? (
                <span className="muted">unconfirmed</span>
              ) : offer.prepayment_penalty ? (
                "yes"
              ) : (
                "no"
              )}
            </dd>
            <dt>Discount clawback</dt>
            <dd className="text">
              {offer.discount_clawback === null ? (
                <span className="muted">unconfirmed</span>
              ) : offer.discount_clawback ? (
                "yes"
              ) : (
                "no"
              )}
            </dd>
            <dt>Minimum term</dt>
            <dd className="text">
              {offer.minimum_loan_months ?? <span className="muted">unconfirmed</span>}
            </dd>
          </dl>
          {(offer.prepayment_penalty === null || offer.discount_clawback === null) && (
            <p className="small" style={{ marginTop: 6 }}>
              Financing to capture the discount and paying it off immediately is not safe to
              plan around until the clawback and prepayment terms are confirmed in writing.
            </p>
          )}
        </div>
      )}

      {p.warnings.map((w, i) => (
        <div key={i} className="callout bad" style={{ marginTop: 8 }}>
          <p>⚠ {w}</p>
        </div>
      ))}
    </>
  );
}

function FactRow({ fact, onOpen }: { fact: Fact; onOpen: () => void }) {
  const superseded = fact.status === "SUPERSEDED";
  return (
    <div className={`fact-row ${superseded ? "superseded" : ""}`} onClick={onOpen}>
      <div>
        <div className="attr">{fact.attribute}</div>
        {fact.quote && <div className="small muted">“{fact.quote}”</div>}
      </div>
      <div style={{ textAlign: "right" }}>
        <div className="val">{fact.display_value}</div>
        <div className="small muted">
          {superseded ? "superseded" : titleCase(fact.method)} · {when(fact.observed_at)}
        </div>
      </div>
    </div>
  );
}

function ProvenanceModal({ factId, onClose }: { factId: number; onClose: () => void }) {
  const provenance = useQuery<Provenance>(`/facts/${factId}/provenance`, [factId]);
  return (
    <Modal title="Why does the system believe this?" onClose={onClose}>
      {provenance.error && <ErrorBox message={provenance.error} />}
      {!provenance.data ? (
        <Loading />
      ) : (
        <>
          <dl className="kv">
            <dt>Attribute</dt>
            <dd>{provenance.data.fact.attribute}</dd>
            <dt>Value</dt>
            <dd>{provenance.data.fact.display_value}</dd>
            <dt>Established by</dt>
            <dd className="text">{titleCase(provenance.data.fact.method)}</dd>
            <dt>Asserted by</dt>
            <dd className="text">{titleCase(provenance.data.fact.asserted_by_party)}</dd>
            <dt>Observed</dt>
            <dd>{when(provenance.data.fact.observed_at)}</dd>
          </dl>

          {provenance.data.fact.quote && (
            <div className="quote">{provenance.data.fact.quote}</div>
          )}

          {provenance.data.interaction && (
            <>
              <h4 style={{ margin: "14px 0 4px" }}>
                Source: {titleCase(provenance.data.interaction.channel)} ·{" "}
                {when(provenance.data.interaction.occurred_at)}
              </h4>
              <div className="small muted">{provenance.data.interaction.subject}</div>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: 12,
                  background: "var(--panel-2)",
                  padding: 10,
                  borderRadius: 6,
                  marginTop: 6,
                }}
              >
                {provenance.data.interaction.normalized_content}
              </pre>
            </>
          )}

          {provenance.data.superseded.length > 0 && (
            <>
              <h4 style={{ margin: "14px 0 4px" }}>Earlier values (never deleted)</h4>
              {provenance.data.superseded.map((f) => (
                <div key={f.id} className="fact-row superseded">
                  <div className="attr">{when(f.observed_at)}</div>
                  <div className="val">{f.display_value}</div>
                </div>
              ))}
            </>
          )}
        </>
      )}
    </Modal>
  );
}

function DraftCard({ draft, onChange }: { draft: Draft; onChange: () => void }) {
  const [body, setBody] = useState(draft.body);
  const [saving, setSaving] = useState(false);
  const dirty = body !== draft.body;

  async function patch(payload: Record<string, unknown>) {
    setSaving(true);
    try {
      await api.patch(`/drafts/${draft.id}`, payload);
      onChange();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginBottom: 14 }}>
      <div className="pill-row" style={{ marginBottom: 5 }}>
        <Chip tone={draft.status === "APPROVED" ? "good" : draft.status === "DISCARDED" ? "bad" : "neutral"}>
          {titleCase(draft.status)}
        </Chip>
        {draft.edited_by_user && <Chip tone="accent">edited</Chip>}
        <span className="small muted">{draft.subject}</span>
      </div>
      {draft.rationale && <div className="quote small">{draft.rationale}</div>}
      <textarea rows={9} value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="pill-row" style={{ marginTop: 6 }}>
        <button className="small" disabled={!dirty || saving} onClick={() => patch({ body })}>
          Save edit
        </button>
        <button
          className="small primary"
          disabled={saving || draft.status === "APPROVED"}
          onClick={() => patch({ status: "APPROVED" })}
        >
          Approve
        </button>
        <button
          className="small danger"
          disabled={saving}
          onClick={() => patch({ status: "DISCARDED" })}
        >
          Discard
        </button>
        <button className="small" onClick={() => navigator.clipboard?.writeText(body)}>
          Copy
        </button>
        <span className="small muted">
          Approving does not send. Paste it into your mail client yourself.
        </span>
      </div>
    </div>
  );
}
