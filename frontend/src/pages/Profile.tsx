import { useEffect, useState } from "react";

import { api, useQuery } from "../api";
import { ErrorBox, Loading, Panel } from "../components/ui";
import { money } from "../format";
import type { BuyerProfile } from "../types";

/** The negotiation profile. The recommender and the tax checks read straight from it. */
export default function Profile() {
  const query = useQuery<BuyerProfile>("/profile");
  const [form, setForm] = useState<BuyerProfile | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (query.data) setForm(query.data);
  }, [query.data]);

  if (query.error) return <ErrorBox message={query.error} />;
  if (!form) return <Loading />;

  function set<K extends keyof BuyerProfile>(key: K, value: BuyerProfile[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setSaved(false);
  }

  async function save() {
    if (!form) return;
    const { id: _id, ...payload } = form;
    try {
      await api.put("/profile", payload);
      setSaved(true);
      setError(null);
      query.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <Panel
        title="Buyer profile"
        count="used when evaluating offers and drafting replies"
        actions={
          <div className="pill-row">
            {saved && <span className="small muted">Saved</span>}
            <button className="small primary" onClick={save}>
              Save
            </button>
          </div>
        }
      >
        {error && <ErrorBox message={error} />}

        <h3 style={{ margin: "0 0 8px", fontSize: 13 }}>What you're buying</h3>
        <div className="form-grid">
          <Field label="Purchase type">
            <select
              value={form.purchase_type}
              onChange={(e) => set("purchase_type", e.target.value)}
            >
              <option value="NEW_ONLY">New only</option>
              <option value="NEW_OR_USED">New or used</option>
              <option value="USED_ONLY">Used only</option>
            </select>
          </Field>
          <Field label="Year">
            <input
              type="number"
              value={form.target_year ?? ""}
              onChange={(e) =>
                set("target_year", e.target.value ? Number(e.target.value) : null)
              }
            />
          </Field>
          <Field label="Make">
            <input value={form.target_make ?? ""} onChange={(e) => set("target_make", e.target.value)} />
          </Field>
          <Field label="Model">
            <input
              value={form.target_model ?? ""}
              onChange={(e) => set("target_model", e.target.value)}
            />
          </Field>
          <Field label="Trim">
            <input value={form.target_trim ?? ""} onChange={(e) => set("target_trim", e.target.value)} />
          </Field>
          <Field label="Powertrain">
            <input
              value={form.target_powertrain ?? ""}
              onChange={(e) => set("target_powertrain", e.target.value)}
              placeholder="NON_HYBRID"
            />
          </Field>
          <Field label="Colour preference">
            <input
              value={form.color_preferences ?? ""}
              onChange={(e) => set("color_preferences", e.target.value)}
              placeholder="dark or subdued"
            />
          </Field>
          <Field label="Excluded colours">
            <input
              value={form.excluded_colors ?? ""}
              onChange={(e) => set("excluded_colors", e.target.value)}
              placeholder="white"
            />
          </Field>
        </div>

        <h3 style={{ margin: "14px 0 8px", fontSize: 13 }}>Money</h3>
        <div className="form-grid">
          <Field label="Registration state">
            <input
              value={form.registration_state ?? ""}
              onChange={(e) => set("registration_state", e.target.value.toUpperCase())}
              maxLength={2}
            />
          </Field>
          <Field label="ZIP">
            <input value={form.zip_code ?? ""} onChange={(e) => set("zip_code", e.target.value)} />
          </Field>
          <Field label="Expected tax rate (basis points)" hint="600 = 6.00%">
            <input
              type="number"
              value={form.expected_tax_rate_bp ?? ""}
              onChange={(e) =>
                set("expected_tax_rate_bp", e.target.value ? Number(e.target.value) : null)
              }
            />
          </Field>
          <Field
            label="Local dealer premium (cents)"
            hint={`${money(form.local_dealer_premium_cents)} — how much extra a local dealer is worth`}
          >
            <input
              type="number"
              value={form.local_dealer_premium_cents}
              onChange={(e) => set("local_dealer_premium_cents", Number(e.target.value || 0))}
            />
          </Field>
          <Field label="Financing stance">
            <select
              value={form.financing_acceptable}
              onChange={(e) => set("financing_acceptable", e.target.value)}
            >
              <option value="NEVER">Never</option>
              <option value="ONLY_IF_ADVANTAGEOUS">Only if it saves money</option>
              <option value="PREFERRED">Preferred</option>
            </select>
          </Field>
          <Field label="Max distance (miles)">
            <input
              type="number"
              value={form.max_distance_miles ?? ""}
              onChange={(e) =>
                set("max_distance_miles", e.target.value ? Number(e.target.value) : null)
              }
            />
          </Field>
        </div>

        <h3 style={{ margin: "14px 0 8px", fontSize: 13 }}>Preferences</h3>
        <div className="form-grid">
          <div>
            <Check
              label="I have a trade-in"
              checked={form.has_trade_in}
              onChange={(v) => set("has_trade_in", v)}
            />
            <Check
              label="Cash available"
              checked={form.cash_available}
              onChange={(v) => set("cash_available", v)}
            />
            <Check
              label="I want dealer add-ons"
              checked={form.wants_add_ons}
              onChange={(v) => set("wants_add_ons", v)}
            />
            <Check
              label="I want a maintenance plan"
              checked={form.wants_maintenance_plan}
              onChange={(v) => set("wants_maintenance_plan", v)}
            />
          </div>
          <div>
            <Check
              label="Avoid phone calls"
              checked={form.avoid_phone_calls}
              onChange={(v) => set("avoid_phone_calls", v)}
            />
            <Check
              label="Avoid dealership visits"
              checked={form.avoid_dealership_visits}
              onChange={(v) => set("avoid_dealership_visits", v)}
            />
          </div>
          <Field label="Follow up after (hours)">
            <input
              type="number"
              value={form.follow_up_after_hours}
              onChange={(e) => set("follow_up_after_hours", Number(e.target.value || 48))}
            />
          </Field>
          <Field label="Treat as no-response after (days)">
            <input
              type="number"
              value={form.no_response_after_days}
              onChange={(e) => set("no_response_after_days", Number(e.target.value || 7))}
            />
          </Field>
        </div>

        <Field label="Notes">
          <textarea
            rows={3}
            value={form.notes ?? ""}
            onChange={(e) => set("notes", e.target.value)}
          />
        </Field>
      </Panel>
    </>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <div className="small muted">{hint}</div>}
    </label>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
