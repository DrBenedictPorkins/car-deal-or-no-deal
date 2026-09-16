import { useState } from "react";
import { Link } from "react-router-dom";

import { api, useQuery } from "../api";
import { Chip, ErrorBox, Loading, Panel } from "../components/ui";
import { when } from "../format";
import type {
  Campaign,
  Dealer,
  InboxCounts,
  InboxMessage,
  PromoteResult,
  SweepReport,
} from "../types";

const QUICK_PICKS = [3, 7, 14, 30];

function toDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toISOString().slice(0, 10);
}

export default function Inbox() {
  const counts = useQuery<InboxCounts>("/inbox/counts");
  const campaign = useQuery<Campaign | null>("/campaigns/active");
  const dealers = useQuery<Dealer[]>("/dealers");
  const [status, setStatus] = useState("NEW");
  const messages = useQuery<InboxMessage[]>(`/inbox?status=${status}`, [status]);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reloadAll() {
    counts.reload();
    messages.reload();
    dealers.reload();
    campaign.reload();
  }

  async function sweep(daysAgo?: number) {
    setBusy(true);
    setError(null);
    try {
      const report = await api.post<SweepReport>("/inbox/sweep", {
        days_ago: daysAgo ?? null,
      });
      setNotice(
        `Swept ${report.fetched} message${report.fetched === 1 ? "" : "s"} since ` +
          `${when(report.since)} — ${report.added} new, ${report.already_known} already seen.`,
      );
      reloadAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function setStartDate(value: string | number) {
    if (!campaign.data) return;
    const body =
      typeof value === "number"
        ? { days_ago: value }
        : { opened_at: new Date(`${value}T00:00:00`).toISOString() };
    await api.patch(`/campaigns/${campaign.data.id}`, body);
    campaign.reload();
    await sweep();
  }

  if (campaign.error) return <ErrorBox message={campaign.error} />;

  return (
    <>
      {!campaign.data ? (
        <NewCampaign onCreated={reloadAll} />
      ) : (
        <Panel
          title="Campaign"
          count={campaign.data.name}
          actions={
            <button className="small primary" onClick={() => sweep()} disabled={busy}>
              {busy ? "Sweeping…" : "Sweep inbox"}
            </button>
          }
        >
          <div className="pill-row" style={{ gap: 10 }}>
            <span className="muted">You first reached out</span>
            <input
              type="date"
              style={{ width: 150 }}
              value={toDateInput(campaign.data.opened_at)}
              onChange={(e) => e.target.value && setStartDate(e.target.value)}
            />
            <span className="muted small">or</span>
            {QUICK_PICKS.map((days) => (
              <button key={days} className="small" onClick={() => setStartDate(days)}>
                {days}d ago
              </button>
            ))}
          </div>
          <p className="small muted" style={{ margin: "8px 0 0" }}>
            The sweep starts here and never moves — your earliest messages are the opening
            offers everything else is measured against. Getting the date wrong is cheap:
            widen it and re-sweep, and anything already seen is skipped rather than
            duplicated. Only senders, subjects and dates are stored; a message body is
            fetched when you claim it and not before.
          </p>
        </Panel>
      )}

      {notice && (
        <div className="panel">
          <div className="panel-body small">{notice}</div>
        </div>
      )}
      {error && <ErrorBox message={error} />}

      <Panel
        title="Inbox"
        count={
          counts.data
            ? `${counts.data.NEW} unclaimed · ${counts.data.PROMOTED} claimed · ` +
              `${counts.data.IGNORED} ignored · source: ${counts.data.ingest_mode}`
            : undefined
        }
        actions={
          <div className="pill-row">
            {["NEW", "PROMOTED", "IGNORED"].map((s) => (
              <button
                key={s}
                className="small"
                style={{ opacity: status === s ? 1 : 0.5 }}
                onClick={() => setStatus(s)}
              >
                {s.toLowerCase()}
              </button>
            ))}
          </div>
        }
        tight
      >
        {messages.error && <ErrorBox message={messages.error} />}
        {!messages.data ? (
          <Loading />
        ) : messages.data.length === 0 ? (
          <div className="empty">
            {status === "NEW"
              ? "Nothing unclaimed. Sweep to pull in what has arrived."
              : `No ${status.toLowerCase()} messages.`}
          </div>
        ) : (
          <ul className="timeline">
            {messages.data.map((message) => (
              <Row
                key={message.id}
                message={message}
                dealers={dealers.data ?? []}
                onChanged={reloadAll}
              />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

function Row({
  message,
  dealers,
  onChanged,
}: {
  message: InboxMessage;
  dealers: Dealer[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(message.suggested_dealer_name ?? "");
  const [foldInto, setFoldInto] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PromoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function claim() {
    setBusy(true);
    setError(null);
    try {
      const body = foldInto
        ? { dealer_id: Number(foldInto) }
        : { dealer_name: name.trim() || null };
      setResult(await api.post<PromoteResult>(`/inbox/${message.id}/promote`, body));
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function ignore() {
    await api.post(`/inbox/${message.id}/ignore`);
    onChanged();
  }

  async function restore() {
    await api.post(`/inbox/${message.id}/restore`);
    onChanged();
  }

  return (
    <li>
      <div className="head">
        {message.score > 0 && (
          <Chip
            tone={message.score >= 4 ? "good" : message.score >= 2 ? "accent" : "neutral"}
            title={message.reasons.join("\n") || undefined}
          >
            {message.score.toFixed(0)}
          </Chip>
        )}
        <strong>{message.from_name ?? message.from_email ?? "unknown sender"}</strong>
        <span className="muted small">{message.from_email}</span>
        <span className="spacer" />
        <time>{when(message.sent_at)}</time>
      </div>

      <div className="subject" style={{ marginTop: 3 }}>
        {message.subject ?? "(no subject)"}
      </div>
      {message.snippet && <p className="preview">{message.snippet}</p>}

      {message.reasons.length > 0 && (
        <div className="why">
          {message.reasons.map((reason, i) => (
            <span key={i}>{reason}</span>
          ))}
        </div>
      )}

      {message.status === "NEW" ? (
        <div className="pill-row" style={{ marginTop: 7 }}>
          {!open ? (
            <>
              <button className="small primary" onClick={() => setOpen(true)}>
                Claim as dealership
              </button>
              <button className="small" onClick={ignore}>
                Not relevant
              </button>
            </>
          ) : (
            <>
              <select
                value={foldInto}
                onChange={(e) => setFoldInto(e.target.value)}
                style={{ width: 210 }}
              >
                <option value="">Create a new dealership…</option>
                {dealers.map((d) => (
                  <option key={d.id} value={d.id}>
                    Fold into {d.name}
                  </option>
                ))}
              </select>
              {!foldInto && (
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Dealership name"
                  style={{ width: 220 }}
                />
              )}
              <button className="small primary" onClick={claim} disabled={busy}>
                {busy ? "Claiming…" : "Confirm"}
              </button>
              <button className="small" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="pill-row" style={{ marginTop: 7 }}>
          <Chip tone={message.status === "PROMOTED" ? "good" : "neutral"}>
            {message.status.toLowerCase()}
          </Chip>
          {message.dealer_id && (
            <Link to={`/dealers/${message.dealer_id}`}>open dealership</Link>
          )}
          <button className="small" onClick={restore}>
            Put back
          </button>
        </div>
      )}

      {error && <div className="error small">{error}</div>}
      {result && (
        <div className="callout" style={{ marginTop: 8 }}>
          <h3>
            {result.created_dealer ? "Created" : "Folded into"} {result.dealer_name}
          </h3>
          <p className="small">
            {result.learned_domain && <>Learned domain {result.learned_domain}. </>}
            {result.offer_id && <>Extracted an offer. </>}
            {result.also_claimed.length > 0 && (
              <>
                Claimed {result.also_claimed.length} more message
                {result.also_claimed.length === 1 ? "" : "s"} from the same thread and
                sender.
              </>
            )}
          </p>
        </div>
      )}
    </li>
  );
}

function NewCampaign({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [days, setDays] = useState<number | null>(7);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      await api.post("/campaigns", {
        name: name.trim() || "Car search",
        days_ago: date ? null : days,
        opened_at: date ? new Date(`${date}T00:00:00`).toISOString() : null,
      });
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Start a campaign">
      <label className="field" style={{ maxWidth: 420 }}>
        <span>What are you shopping for?</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="2026 Honda Civic Hatchback Sport"
        />
      </label>

      <label className="field">
        <span>When did you first reach out?</span>
      </label>
      <div className="pill-row">
        {QUICK_PICKS.map((d) => (
          <button
            key={d}
            className={days === d && !date ? "small primary" : "small"}
            onClick={() => {
              setDays(d);
              setDate("");
            }}
          >
            {d} days ago
          </button>
        ))}
        <span className="muted small">or pick a date</span>
        <input
          type="date"
          style={{ width: 150 }}
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>
      <p className="small muted" style={{ margin: "8px 0 12px" }}>
        Roughly is fine. This anchors how far back the inbox sweep reaches, and you can
        change it afterwards — re-sweeping a range you have already covered adds only what
        was missing.
      </p>
      <button className="primary" onClick={create} disabled={busy}>
        {busy ? "Creating…" : "Create campaign"}
      </button>
    </Panel>
  );
}
