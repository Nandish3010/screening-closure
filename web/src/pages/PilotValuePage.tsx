import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { DOCS, docHref } from "../judge/links";
import { PAGE_DOCS } from "../judge/pageLinks";
import headline from "../judge/headline.json";
import { DEFAULT_INPUTS, PILOT, ROLE_LABEL, aed, compute, int, pct, type PilotInputs, type RoleMinutes } from "../lib/pilotValue";
import "../judgePages.css";

/**
 * Pilot value calculator (item 13). Illustrative and synthetic. The formula is a port of tools/pilot/cost_model.py
 * (src/lib/pilotValue.ts); every default and assumption shown here is read from src/judge/pilotValue.json, which
 * `make judge-pages-data` generates from tools/pilot/inputs.yaml and docs/results/*.json. Nothing is fetched.
 */
const COST_DOC = PAGE_DOCS.businessCase;
const GTM_DOC = PAGE_DOCS.goToMarket;
const PLAN_DOC = PAGE_DOCS.pilotPlan;
const MODEL_DOC = PAGE_DOCS.costInputs;

const ROLE_KEYS = ["coordinator", "owner_clinician", "specialist"] as const;
type FormState = Record<keyof PilotInputs, string>;

const toForm = (i: PilotInputs): FormState => ({
  annualPositives: String(Math.round(i.annualPositives)),
  currentCompletion: (i.currentCompletion * 100).toFixed(1),
  staffCostPerHour: String(i.staffCostPerHour),
  pilotCases: String(Math.round(i.pilotCases)),
});

function parse(f: FormState): { value: PilotInputs; errors: Partial<Record<keyof PilotInputs, string>> } {
  const errors: Partial<Record<keyof PilotInputs, string>> = {};
  const num = (k: keyof PilotInputs, min: number, max: number, label: string) => {
    const x = Number(f[k]);
    if (f[k].trim() === "" || !Number.isFinite(x) || x < min || x > max) {
      errors[k] = `${label} must be a number from ${min.toLocaleString("en-US")} to ${max.toLocaleString("en-US")}.`;
      return min;
    }
    return x;
  };
  const value: PilotInputs = {
    annualPositives: num("annualPositives", 0, 1_000_000, "Annual positive results"),
    currentCompletion: num("currentCompletion", 0, 100, "Current follow-up completion") / 100,
    staffCostPerHour: num("staffCostPerHour", 0, 10_000, "Staff cost per hour"),
    pilotCases: num("pilotCases", 1, 100_000, "Planned pilot cases"),
  };
  return { value, errors };
}

function Field({ id, label, hint, value, onChange, error, step }: {
  id: string; label: string; hint: string; value: string; onChange: (v: string) => void; error?: string; step: string;
}) {
  return (
    <div className="jp-field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="number" inputMode="decimal" step={step} value={value} onChange={(e) => onChange(e.target.value)}
        aria-describedby={`${id}-hint`} aria-invalid={error ? true : undefined} />
      <span id={`${id}-hint`} className="jp-hint">{error ? <span role="alert">{error}</span> : hint}</span>
    </div>
  );
}

function RoleTable({ baseline, engine }: { baseline: RoleMinutes; engine: RoleMinutes }) {
  const red = PILOT.effort.all.reduction;
  return (
    <div className="jp-tablewrap">
      <table className="jp-table" data-testid="pilot-minutes">
        <caption>Staff minutes a year by role (effort model, synthetic)</caption>
        <thead><tr><th scope="col">Role</th><th scope="col" className="num">Today (baseline)</th><th scope="col" className="num">With the engine</th><th scope="col" className="num">Change</th></tr></thead>
        <tbody>
          {ROLE_KEYS.map((r) => {
            const change = red[r];
            return (
              <tr key={r}>
                <th scope="row">{ROLE_LABEL[r]}</th>
                <td className="num">{int(baseline[r])}</td>
                <td className="num">{int(engine[r])}</td>
                <td className="num">{change >= 0 ? `${pct(change)} fewer` : `${pct(-change)} more`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PilotValuePage() {
  const uid = useId();
  const [form, setForm] = useState<FormState>(() => toForm(DEFAULT_INPUTS));
  const { value, errors } = parse(form);
  const out = compute(value);
  const set = (k: keyof PilotInputs) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const reduction = PILOT.effort.all.reduction.labour_reduction;
  const target = PILOT.effort.target_reduction;
  const targetMet = reduction >= target;
  const days = PILOT.inputs.shadow_days.value;
  const providers = PILOT.inputs.pilot_providers.value;
  const isDefault = JSON.stringify(form) === JSON.stringify(toForm(DEFAULT_INPUTS));
  const s = out.shadow;
  const shown: (keyof typeof PILOT.inputs)[] = [
    "abu_dhabi_population_40_to_75", "fit_participation_annual", "fit_positivity_market", "pilot_providers",
    "coordinator_cost_aed_per_hour", "shadow_days", "shadow_integration_one_off_aed_per_provider", "shadow_support_fte",
    "shadow_governance_review_one_off_aed", "third_party_licences_aed_per_year",
  ];

  return (
    <main className="container" data-testid="pilot-value-page">
      <div className="page-head">
        <h1>Pilot value</h1>
        <span className="chip chip-synthetic">Synthetic data</span>
        <span className="chip chip-warn">Illustrative</span>
      </div>
      <p className="jp-lead">
        Try your own numbers. The calculator shows how many more people might complete follow-up, how staff time moves by role,
        and what the {days}-day shadow run costs to serve. It runs in your browser and sends nothing anywhere.
      </p>
      <div className="banner banner-warn" data-testid="pilot-illustrative">
        <strong>Illustrative, synthetic.</strong> The completion and staff-minute effects come from a simulator calibrated to published studies, not
        from patients or a trial. The costs are assumptions set out in the cost model document (linked below), not quotes. Nothing here is a forecast or an offer.
      </div>

      <section aria-labelledby={`${uid}-in`} className="jp-section">
        <h2 id={`${uid}-in`}>Your inputs</h2>
        <p className="jp-note">
          {isDefault ? "These are the repository's documented Abu Dhabi scenario." : "You have changed the defaults."}{" "}
          <button type="button" className="btn btn-small" onClick={() => setForm(toForm(DEFAULT_INPUTS))} disabled={isDefault}>Reset to the documented scenario</button>
        </p>
        <form className="jp-form" onSubmit={(e) => e.preventDefault()} aria-label="Pilot value inputs" noValidate>
          <Field id={`${uid}-pos`} label="Annual positive results" step="1" value={form.annualPositives} onChange={set("annualPositives")} error={errors.annualPositives}
            hint={`Default: ${int(PILOT.defaults.annual_positives)}, from ${int(PILOT.inputs.abu_dhabi_population_40_to_75.value)} residents aged 40 to 75, ${pct(PILOT.inputs.fit_participation_annual.value, 0)} with a FIT result a year, ${pct(PILOT.inputs.fit_positivity_market.value, 0)} positive.`} />
          <Field id={`${uid}-cur`} label="Current follow-up completion (%)" step="any" value={form.currentCompletion} onChange={set("currentCompletion")} error={errors.currentCompletion}
            hint={`Share of positives who complete the follow-up colonoscopy within 360 days. Default: ${pct(PILOT.completion.baseline_360d)}, the simulated baseline matched to a published Abu Dhabi figure (${PILOT.completion.published_followed_up} of ${int(PILOT.completion.published_positives)}).`} />
          <Field id={`${uid}-rate`} label="Staff cost per hour (AED)" step="1" value={form.staffCostPerHour} onChange={set("staffCostPerHour")} error={errors.staffCostPerHour}
            hint="Default: the cost model's loaded coordinator rate (an assumption). All roles are priced at this one rate." />
          <Field id={`${uid}-cases`} label="Planned pilot cases" step="1" value={form.pilotCases} onChange={set("pilotCases")} error={errors.pilotCases}
            hint={`Positive results expected in the ${days}-day run. Default: ${providers} providers at placeholder volumes.`} />
        </form>
      </section>

      <div className="jp-split">
        <div>
          <section aria-labelledby={`${uid}-out`} className="jp-section" aria-live="polite">
            <h2 id={`${uid}-out`}>What it shows</h2>
            <dl className="jp-kpis" data-testid="pilot-kpis">
              <div className="jp-kpi">
                <dt>Extra completed follow-ups a year</dt>
                <dd>
                  <span className="jp-big" data-testid="pilot-extra">{int(out.extraCompleted)}</span>
                  <span className="jp-small">Completion goes from {pct(value.currentCompletion)} to {pct(out.completionWithEngine)} (a ratio of {PILOT.completion.ratio_360d.toFixed(2)}, simulated).</span>
                </dd>
              </div>
              <div className="jp-kpi">
                <dt>Staff time with the engine</dt>
                <dd>
                  <span className="jp-big" data-testid="pilot-reduction">{pct(out.reduction)} {out.reduction >= 0 ? "less" : "more"}</span>
                  <span className="jp-small">{int(out.baselineTotalMinutes / 60)} hours today, {int(out.engineTotalMinutes / 60)} hours with the engine, a year.</span>
                </dd>
              </div>
              <div className="jp-kpi">
                <dt>Cost to serve the {days}-day shadow run</dt>
                <dd>
                  <span className="jp-big" data-testid="pilot-shadow-total">{aed(s.total)}</span>
                  <span className="jp-small">{int(s.cases)} cases, {aed(s.costPerCase)} per case.</span>
                </dd>
              </div>
            </dl>

            <div className={`banner ${targetMet ? "banner-warn" : "banner-bad"}`} data-testid="pilot-target">
              <strong>{targetMet ? "Read the labour result with its caveats." : `The ${pct(target, 0)} labour-reduction target is not met.`}</strong>{" "}
              The effort model's reduction in staff time is {pct(reduction)}, below {pct(target, 0)}. Coordinator minutes fall ({pct(PILOT.effort.all.reduction.coordinator)}), but owner-clinician minutes rise ({pct(-PILOT.effort.all.reduction.owner_clinician)} more) and so do specialist minutes ({pct(-PILOT.effort.all.reduction.specialist)} more). The minutes per action are assumed, not measured; the pilot would measure them.
            </div>

            <RoleTable baseline={out.baselineMinutes} engine={out.engineMinutes} />
            <p className="jp-note" data-testid="pilot-staff-cost">
              At {aed(value.staffCostPerHour)} an hour for every role, that is {aed(out.baselineStaffCost)} of staff time a year today and {aed(out.engineStaffCost)} with the engine.
              Pricing every role at one rate is the cost model's own simplification and understates clinician and specialist time. This is not a saving the product claims: the case for paying is safety and timelines, not avoided cost.
            </p>

            <div className="jp-tablewrap">
              <table className="jp-table" data-testid="pilot-shadow-table">
                <caption>Cost to serve a {days}-day shadow run for {int(s.cases)} cases ({providers} providers, read-only)</caption>
                <thead><tr><th scope="col">Item</th><th scope="col" className="num">AED</th></tr></thead>
                <tbody>
                  <tr><th scope="row">Hosting</th><td className="num">{int(s.hosting)}</td></tr>
                  <tr><th scope="row">Third-party licences</th><td className="num">{int(s.licences)}</td></tr>
                  <tr><th scope="row">Support</th><td className="num">{int(s.support)}</td></tr>
                  <tr><th scope="row">Integration (one-off, export mapping)</th><td className="num">{int(s.integration)}</td></tr>
                  <tr><th scope="row">Governance review (one-off)</th><td className="num">{int(s.governance)}</td></tr>
                  <tr><th scope="row">Total</th><td className="num"><strong>{int(s.total)}</strong></td></tr>
                </tbody>
              </table>
            </div>
            <p className="jp-note">
              The shadow run reads a periodic export and sends nothing, so it costs far less than an active pilot. The programme licence that could follow is a separate, later conversation
              ({aed(PILOT.inputs.programme_licence_aed_per_year.value)} a year for {PILOT.inputs.licence_pathways.value} pathways, illustrative); it is not part of this cost.
              Fixed costs do not shrink with fewer cases, so the cost per case rises as the case count falls.
            </p>
          </section>
        </div>

        <aside className="jp-aside" aria-labelledby={`${uid}-as`} data-testid="pilot-assumptions">
          <h3 id={`${uid}-as`}>Assumptions behind the numbers</h3>
          <p className="jp-note">Each input is graded in the cost model. "Assumed" means no source; "sourced-adjacent" means a nearby published figure.</p>
          <dl>
            {shown.map((k) => {
              const a = PILOT.inputs[k];
              return (
                <div key={k}>
                  <dt>{k.replace(/_/g, " ")}: <span className="mono">{typeof a.value === "number" ? a.value.toLocaleString("en-US") : String(a.value)}</span><span className="jp-status">{a.status}</span></dt>
                  <dd>{a.unit}<details><summary>Basis</summary>{a.basis}</details></dd>
                </div>
              );
            })}
            <div>
              <dt>Completion ratio: <span className="mono">{PILOT.completion.ratio_360d.toFixed(2)}</span><span className="jp-status">synthetic</span></dt>
              <dd>Orchestrated over baseline completion at 360 days in the Abu Dhabi scenario of the published results. Calibrated to published navigation trials, so it shows the mechanism and is not new evidence of effect.</dd>
            </div>
            <div>
              <dt>Minutes per case by role<span className="jp-status">synthetic</span></dt>
              <dd>From the published results: effort, literature-anchored, all cases. The minutes per action are assumptions drawn from time studies where they exist.</dd>
            </div>
          </dl>
        </aside>
      </div>

      <section aria-labelledby={`${uid}-cmp`} className="jp-section" data-testid="pilot-compare">
        <h2 id={`${uid}-cmp`}>How this compares to today's tools</h2>
        <p className="jp-note">Only claims the repository already makes are used here, and no products are named. None of this is a measured comparison with a real tool.</p>
        <div className="jp-tablewrap">
          <table className="jp-table">
            <caption>Three ways follow-up is handled today, and what the repository says about each</caption>
            <thead><tr><th scope="col">Today</th><th scope="col">What the repository says</th><th scope="col">Where</th></tr></thead>
            <tbody>
              <tr>
                <th scope="row">Manual tracking</th>
                <td>In the simulation, the baseline arm (follow-up as it is done without the engine) loses {headline.silent_loss.baseline.value} of cases to follow-up with no one shown to be responsible, against {headline.silent_loss.orchestrated_programme.value} at programme level with the engine. The simulator is synthetic and calibrated to published studies.</td>
                <td><a href={docHref(DOCS.results)} target="_blank" rel="noopener noreferrer">Evaluation results</a></td>
              </tr>
              <tr>
                <th scope="row">Worklists in an electronic health record</th>
                <td>The go-to-market plan expects a buyer to say its system already does this. Its stated position is on the guarantees: no closure without evidence, a clock that spans providers, and an audit trail, plus reporting against the national timelines. The plan is to integrate with such a system rather than replace it.</td>
                <td><a href={docHref(GTM_DOC)} target="_blank" rel="noopener noreferrer">Go-to-market, risk R9</a></td>
              </tr>
              <tr>
                <th scope="row">Generic reminders</th>
                <td>The simulation's literature-anchored scenario treats text reminders as adding nothing beyond the no-reminder attendance rate, the cautious end of the published range. What this product adds is an owner, a clock, an escalation path and a clinician-signed closure, not a message alone.</td>
                <td><a href={docHref(DOCS.results)} target="_blank" rel="noopener noreferrer">Evaluation results</a></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby={`${uid}-docs`} className="jp-section">
        <h2 id={`${uid}-docs`}>Where the numbers come from</h2>
        <ul className="plain jp-links">
          {[MODEL_DOC, COST_DOC, GTM_DOC, PLAN_DOC, DOCS.results].map((d) => (
            <li key={d.path}><a href={docHref(d)} title={d.path} target="_blank" rel="noopener noreferrer">{d.label}</a></li>
          ))}
        </ul>
        <p className="jp-note">The data behind this page is regenerated from the published results, and the cost model can be re-run from the repository.</p>
        <p><Link to="/judge">Back to the judge summary</Link></p>
      </section>
    </main>
  );
}
