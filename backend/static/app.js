const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const inr = n => "₹" + Number(n).toLocaleString("en-IN");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ROUTE = {AUTO_CLEAR:"Auto-clear", CUSTOMER_VERIFY:"Customer verification", ANALYST_REVIEW:"Analyst review"};
const STATUS = {NEW:"Incoming", INVESTIGATING:"AI investigating", RELEASED:"Released", AWAITING_CUSTOMER:"Awaiting customer", ANALYST_REVIEW:"With analyst", BLOCKED:"Blocked"};
const DEC = {PROCEED:"Proceed", VERIFY:"Verify", HUMAN_REVIEW:"Human review"};
const HARD = ["NEW_BENEFICIARY", "RECENT_CONTACT_CHANGE"];
const pill = (k, map) => k ? `<span class="pill ${k}">${esc((map||{})[k] || k)}</span>` : "";
const tm = iso => new Date(iso).toLocaleString("en-IN", {dateStyle:"medium", timeStyle:"short", timeZone:"Asia/Kolkata"}) + " IST";
let view = "demo", next = null, cur = null, curStatus = null, tab = "evidence", pollUntil = 0, stats = null, seen = new Set(), busy = false;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const post = (p, body) => api(p, {method:"POST", headers:{"Content-Type":"application/json"}, body: body ? JSON.stringify(body) : undefined});
function toast(m){ const t=$("#toast"); t.textContent=m; t.classList.remove("hidden"); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.add("hidden"),3500); }

// ---------- stats + queue ----------
async function loadStats() {
  stats = await api("/api/stats");
  const s = stats, pct = s.total ? Math.round(100 * s.tier1 / s.total) : 0;
  $("#kpis").innerHTML = [
    ["blue", s.total, "Payments processed"],
    ["ok", `${s.tier1} <small>(${pct}%)</small>`, "Cleared at Tier 1 — no AI, no staff"],
    ["blue", s.ai, "Gemini investigations"],
    ["live", (s.status.INVESTIGATING ? '<span class="spin"></span>' : "") + s.status.INVESTIGATING, "In AI queue (Pub/Sub)"],
    ["warn", s.route.CUSTOMER_VERIFY, "Routed to customer self-verification"],
    ["bad", s.route.ANALYST_REVIEW, "Routed to analyst"],
  ].map(([cl, v, l]) => `<div class="kpi ${cl}"><b>${v}</b><span>${l}</span></div>`).join("");
  const F = [["demo","Demo cases"],["all",`All · ${s.total}`],["INVESTIGATING",`AI queue · ${s.status.INVESTIGATING}`],
    ["AWAITING_CUSTOMER",`Awaiting customer · ${s.status.AWAITING_CUSTOMER}`],["ANALYST_REVIEW",`With analyst · ${s.status.ANALYST_REVIEW}`],
    ["RELEASED",`Released · ${s.status.RELEASED}`],["BLOCKED",`Blocked · ${s.status.BLOCKED}`]];
  $("#chips").innerHTML = F.map(([k, l]) => `<span class="chip ${view===k?"on":""}" data-f="${k}">${l}</span>`).join("");
  $$(".chip").forEach(e => e.onclick = () => { view = e.dataset.f; loadStats(); loadCases(); });
  $("#mode").textContent = s.mode === "pubsub" ? "Pub/Sub · async" : "local mode";
  const u = s.usage; $("#usage").textContent = `Today: ${u.feed_payments}/${u.feed_cap} feed payments · ${u.ai_investigations}/${u.ai_cap} AI investigations (cost cap)`;
  return s;
}
function caseCard(x) {
  const fresh = !seen.has(x.id) && seen.size; seen.add(x.id);
  return `<div class="case s-${x.status} ${cur===x.id?"sel":""} ${fresh?"flash":""}" data-id="${x.id}">
   <div class="row"><b>${x.id}</b><span class="amt">${inr(x.amount)}</span></div>
   <div class="muted">${esc(x.customer_name)} → ${esc(x.payee_name)}</div>
   <div>${(x.rules||[]).map(r => `<span class="tag ${HARD.includes(r)?"hard":""}">${r}</span>`).join("") || '<span class="tag">no rule hits</span>'}
     ${x.tier === 1 ? '<span class="tag">Tier 1 · no AI</span>' : x.tier === 2 ? '<span class="tag ai">Gemini</span>' : ""}</div>
   <div class="row" style="margin-top:6px"><small>${esc(x.channel)}</small><span>${x.status==="INVESTIGATING"?'<span class="spin"></span>':""}${pill(x.status, STATUS)}</span></div></div>`;
}
async function loadCases(more = false) {
  const q = `/api/cases?view=${view}&limit=20` + (more && next ? `&cursor=${encodeURIComponent(next)}` : "");
  const r = await api(q); next = r.next;
  const html = r.items.map(caseCard).join("");
  if (more) $("#cases").insertAdjacentHTML("beforeend", html);
  else $("#cases").innerHTML = html || '<p class="muted">Nothing here yet.</p>';
  $("#more").classList.toggle("hidden", !next);
  $$(".case").forEach(e => e.onclick = () => openCase(e.dataset.id));
}
$("#more").onclick = () => loadCases(true);

// ---------- detail ----------
async function openCase(id, quiet) {
  if (!quiet) tab = "evidence";
  cur = id; $$(".case").forEach(e => e.classList.toggle("sel", e.dataset.id === id));
  const d = await api(`/api/case/${id}`); curStatus = d.status;
  if (d.status === "NEW") return renderNew(d);
  if (d.status === "INVESTIGATING" || !d.inv) return renderInvestigating(d);
  render(d);
}
function head(c, st) {
  return `<div class="dh"><div><h2>${c.id} · ${inr(c.amount)}</h2><div class="muted">${esc(c.customer_name)} → ${esc(c.payee_name)} · ${esc(c.channel)} · ${tm(c.at)}</div></div>
    <div>${st==="INVESTIGATING"?'<span class="spin"></span>':""}${pill(st, STATUS)}</div></div>`;
}
function renderNew(d) {
  const c = d.case;
  $("#detail").innerHTML = `${head(c, "NEW")}${steps(null)}<div class="callout info">Not screened yet. Static rules alone would say: ${pill(c.rules_only_decision, DEC)}</div>
    <div class="action"><b>Run the pipeline</b><div class="muted">Tier 1 rules + behaviour profile run instantly; Gemini is called only if Tier 1 can't clear it.</div>
    <div class="btns"><button class="btn" id="go">Screen payment</button></div></div>`;
  $("#go").onclick = () => screen(c.id);
}
function renderInvestigating(d) {
  const c = d.case;
  $("#detail").innerHTML = `${head(c, "INVESTIGATING")}
    <div class="steps"><div class="step flag"><div class="n">STEP 1</div><b>Static rules</b><span>${(c.rules||[]).join(", ") || "no hits"}</span></div>
    <div class="step flag"><div class="n">STEP 2</div><b>Behaviour profile</b><span>Flagged by Tier 1</span></div>
    <div class="step run"><div class="n">STEP 3</div><b>Gemini agent</b><span>Queued on Pub/Sub → worker</span></div>
    <div class="step"><div class="n">STEP 4</div><b>Citation check</b><span>Pending</span></div><div class="step"><div class="n">STEP 5</div><b>Route</b><span>Pending</span></div></div>
    <div class="callout info"><span class="spin"></span>Tier 1 could not clear this payment, so it was published to the <b>flagged-payments</b> topic. A Gemini worker is investigating it asynchronously; this view refreshes automatically.</div>
    <h3>Audit</h3>${auditTable(d.audit, false)}`;
}
async function screen(id) {
  $("#go").disabled = true;
  let i = 0; const tick = setInterval(() => { i = Math.min(i + 1, 2); const s = $("#detail .steps"); if (s) s.outerHTML = steps(null, i); }, 450);
  try { const d = await post(`/api/process/${id}`); clearInterval(tick); await refresh(); render(d); }
  catch (e) { clearInterval(tick); toast("Error: " + e.message); openCase(id); }
}
function steps(r, running) {
  const S = (n, t, sub, cls) => `<div class="step ${cls}"><div class="n">STEP ${n}</div><b>${t}</b><span>${sub}</span></div>`;
  if (!r) { const st = k => running === undefined ? "" : k < running ? "done" : k === running ? "run" : "";
    return `<div class="steps">${S(1,"Static rules","Policy checks",st(0))}${S(2,"Behaviour profile","Customer's own history",st(1))}${S(3,"AI investigation","Only if needed",st(2))}${S(4,"Citation check","Claims vs records","")}${S(5,"Route","Next step","")}</div>`; }
  const hard = r.rule_hits.filter(h => h.hard).length, dev = r.behaviour_signals.filter(b => b.signal !== "CONSISTENT_WITH_HISTORY").length, g = r.grounding || {};
  return `<div class="steps">
    ${S(1,"Static rules", r.rule_hits.length ? `${r.rule_hits.length} hit(s), ${hard} mandatory` : "No hits", r.rule_hits.length ? "flag" : "done")}
    ${S(2,"Behaviour profile", dev ? `${dev} deviation(s)` : "Matches history", dev ? "flag" : "done")}
    ${r.ai_used ? S(3,"Gemini agent", r.ai_available ? `${r.trace.length} tool calls · ${(r.latency_ms/1000).toFixed(1)} s` : "Unavailable — rules-only", "done") : S(3,"Gemini agent","Skipped — Tier 1 cleared","skip")}
    ${r.ai_used && r.ai_available ? S(4,"Citation check", `${g.supported ?? 0} cited · ${g.unsupported ?? 0} removed`, "done") : S(4,"Citation check","Not needed","skip")}
    ${S(5,"Route", ROUTE[r.route] || "-", "done")}</div>`;
}
function render(d) {
  const r = d.inv, rep = r.report || {}, st = d.status, c = d.case;
  const L = {PROCEED:0, VERIFY:1, HUMAN_REVIEW:2};
  let impact = "";
  if (L[r.final_recommendation] < L[r.rules_only_decision]) impact = `<div class="callout ok"><b>Friction avoided:</b> static rules alone would force “${DEC[r.rules_only_decision]}”. The customer's own history shows this payment is routine, so it was cleared ${r.ai_used ? "after investigation" : "instantly at Tier 1, without an AI call"}.</div>`;
  else if (L[r.final_recommendation] > L[r.rules_only_decision]) impact = `<div class="callout bad"><b>Escalated beyond static rules</b> based on the investigation.</div>`;
  else if (r.final_recommendation !== "PROCEED") impact = `<div class="callout info">Rules and investigation agree. The investigation adds the evidence and the checks to run.</div>`;
  $("#detail").innerHTML = `${head(c, st)}${steps(r)}
   <div class="hero">
     <div><small>Static rules only</small>${pill(r.rules_only_decision, DEC)}</div>
     <div><small>Mandatory minimum (hard rules)</small>${pill(r.mandatory_floor, DEC)}</div>
     <div class="main"><small><b>RiskRoute decision</b> ${r.ai_used ? (r.ai_available ? "· Gemini " + esc(r.model) : "· rules-only fallback") : "· Tier 1 (no AI)"}</small>${pill(r.route, ROUTE)}</div></div>
   ${impact}
   ${r.guard_note ? `<div class="callout warn"><b>Guardrail:</b> ${esc(r.guard_note)}</div>` : ""}
   ${rep.summary ? `<div class="callout info"><b>Summary:</b> ${esc(rep.summary)}</div>` : ""}
   ${actionPanel(d)}
   <div class="tabs">${[["evidence","Evidence"],["trace","Agent trace"],["signals","Signals"],["checks","Gaps & checks"],["audit",`Case audit (${d.audit.length})`]].map(([k,l]) => `<button data-t="${k}" class="${tab===k?"on":""}">${l}</button>`).join("")}</div>
   <div class="tab ${tab==="evidence"?"on":""}" id="t-evidence">
     <div class="grid2"><div><h3>Risk indicators</h3>${claims(rep.risk_indicators, "risk")}</div><div><h3>Reduces concern</h3>${claims(rep.mitigating_factors, "mit")}</div></div>
     ${rep.reasoning ? `<h3>Reasoning</h3><div>${esc(rep.reasoning)}</div>` : ""}
     ${secondOpinion(d)}</div>
   <div class="tab ${tab==="trace"?"on":""}" id="t-trace">${r.ai_used && r.ai_available ? `<ol class="trace">${r.trace.map(t => `<li><b>${t.tool}</b>(${esc(Object.values(t.args).join(", "))}) → ${esc(t.new_evidence.join(", ") || "no new records")}</li>`).join("")}</ol>
      <div class="muted">Gemini chose these tool calls itself. Records returned here are the only IDs a claim may cite.</div>` : `<p class="muted">No AI call — ${r.ai_used ? esc(r.guard_note) : "Tier 1 cleared this payment deterministically in " + r.latency_ms + " ms."}</p>`}</div>
   <div class="tab ${tab==="signals"?"on":""}" id="t-signals"><h3>Static rules</h3>${r.rule_hits.map(h => `<div class="tag ${h.hard?"hard":""}">${h.hard?"MANDATORY · ":""}${h.rule}: ${esc(h.detail)}</div>`).join("") || '<span class="muted">No rule hits</span>'}
     <h3>Behaviour vs customer profile</h3>${r.behaviour_signals.map(b => `<div class="tag">${b.signal}: ${esc(b.detail)}</div>`).join("")}</div>
   <div class="tab ${tab==="checks"?"on":""}" id="t-checks"><div class="grid2"><div><h3>Missing information</h3><ul>${(rep.missing_information||[]).map(m=>`<li>${esc(m)}</li>`).join("")||"<li class='muted'>None</li>"}</ul></div>
     <div><h3>Recommended checks</h3><ul>${(rep.recommended_checks||[]).map(m=>`<li>${esc(m)}</li>`).join("")||"<li class='muted'>None</li>"}</ul></div></div></div>
   <div class="tab ${tab==="audit"?"on":""}" id="t-audit">${auditTable(d.audit, false)}</div>`;
  $$(".tabs button").forEach(b => b.onclick = () => { tab = b.dataset.t; $$(".tabs button").forEach(x => x.classList.toggle("on", x===b)); $$(".tab").forEach(t => t.classList.toggle("on", t.id === "t-" + tab)); });
  bindActions(d);
}
const claims = (arr, cls) => (arr || []).map(c => `<div class="claim ${cls}">${esc(c.claim)}${(c.evidence||[]).map(e => `<span class="ev">[${esc(e)}]</span>`).join("")}</div>`).join("") || '<div class="muted">None</div>';
function secondOpinion(d) {
  if (d.inv.ai_used) return "";
  const o = d.ai_second_opinion;
  if (!o) return `<div class="action"><b>Want Gemini's view anyway?</b><div class="muted">Optional second opinion on a Tier-1 clear. It does not change the route.</div><div class="btns"><button class="btn ghost" id="ai2">Ask Gemini</button></div></div>`;
  const rp = o.report || {};
  return `<h3>Gemini second opinion · ${pill(o.ai_recommendation, DEC)}</h3><div class="callout info">${esc(rp.summary)}</div><div class="grid2"><div>${claims(rp.risk_indicators,"risk")}</div><div>${claims(rp.mitigating_factors,"mit")}</div></div>`;
}
function actionPanel(d) {
  const r = d.inv, st = d.status, contact = r.rule_hits.some(h => h.rule === "RECENT_CONTACT_CHANGE");
  if (st === "RELEASED") return `<div class="callout ok"><b>Released.</b> ${r.route === "AUTO_CLEAR" && d.audit.length <= 1 ? "Auto-cleared by policy — no staff involved." : "Closed after verification/review."} <small>(Simulated — no money moved. Customer profile updated.)</small></div>`;
  if (st === "BLOCKED") return `<div class="callout bad"><b>Blocked.</b> Payment stopped and flagged for follow-up. <small>(Simulated.)</small></div>`;
  if (st === "AWAITING_CUSTOMER") return `<div class="action"><b>Waiting for the customer to confirm</b>
     <div class="muted">A confirmation request was sent to the customer's registered app/phone. No bank staff needed unless the customer says no.</div>
     <div class="btns"><button class="btn warn" id="a-phone">Open customer's phone (simulate)</button><button class="btn ghost" id="a-esc">Escalate to analyst</button></div></div>`;
  if (st === "ANALYST_REVIEW") return `<div class="action"><b>Analyst decision</b> <small>— the investigation below is already prepared</small>
     ${contact ? '<div class="callout warn">Registered mobile changed recently: a customer confirmation could reach the attacker. Prefer an out-of-band call to a previously verified number.</div>' : ""}
     <textarea id="a-note" placeholder="Note for the audit log (optional)"></textarea>
     <div class="btns"><button class="btn ok" id="a-rel">Release payment</button><button class="btn bad" id="a-blk">Block payment</button><button class="btn ghost" id="a-ver">Request customer verification</button></div></div>`;
  return "";
}
function bindActions(d) {
  const id = d.case.id, note = () => ($("#a-note")?.value || "");
  const run = async (action, msg) => { try { const nd = await post(`/api/case/${id}/action`, {action, note: note()}); await refresh(); render(nd); toast(msg); } catch (e) { toast(e.message); } };
  $("#a-phone") && ($("#a-phone").onclick = () => phone(d, run));
  $("#a-esc") && ($("#a-esc").onclick = () => run("ESCALATE", "Escalated to analyst"));
  $("#a-rel") && ($("#a-rel").onclick = () => run("ANALYST_RELEASE", "Payment released (simulated)"));
  $("#a-blk") && ($("#a-blk").onclick = () => run("ANALYST_BLOCK", "Payment blocked"));
  $("#a-ver") && ($("#a-ver").onclick = () => run("REQUEST_VERIFICATION", "Verification sent to customer"));
  $("#ai2") && ($("#ai2").onclick = async () => { $("#ai2").disabled = true; $("#ai2").textContent = "Gemini is investigating…";
    try { const nd = await post(`/api/ai_opinion/${id}`); render(nd); } catch (e) { toast(e.message); $("#ai2").disabled = false; $("#ai2").textContent = "Ask Gemini"; } });
}
function phone(d, run) {
  const r = d.inv, c = d.case, msg = r.report?.customer_verification_message;
  $("#ph-body").innerHTML = `<div class="muted">Did you just request this payment?</div><div class="amt">${inr(c.amount)}</div>
    <div>to <b>${esc(c.payee_name)}</b></div><div class="muted">${esc(c.channel)} · ${tm(c.at)}</div>
    ${msg ? `<hr><div style="font-size:13px">${esc(msg)}</div><small>Message drafted by Gemini</small>` : ""}
    <hr><small>We will never ask for your OTP, PIN or password.</small>`;
  $("#modal").classList.remove("hidden");
  $("#ph-yes").onclick = () => { $("#modal").classList.add("hidden"); run("CUSTOMER_CONFIRMED", "Customer confirmed"); };
  $("#ph-no").onclick = () => { $("#modal").classList.add("hidden"); run("CUSTOMER_DENIED", "Customer denied — payment blocked"); };
}
$("#ph-close").onclick = () => $("#modal").classList.add("hidden");

function auditTable(rows, withCase = true) {
  if (!rows.length) return '<p class="muted">No actions yet.</p>';
  return `<table><tr><th>Time</th>${withCase?"<th>Case</th>":""}<th>Action</th><th>Actor</th><th>Status</th><th>Note</th></tr>
   ${rows.map(a => `<tr><td>${tm(a.at)}</td>${withCase?`<td><b class="lnk" data-open="${a.txn}">${a.txn}</b></td>`:""}<td>${a.action}</td><td>${esc(a.actor)}</td><td>${pill(a.from, STATUS)} → ${pill(a.to, STATUS)}</td><td>${esc(a.note)}</td></tr>`).join("")}</table>`;
}

// ---------- live feed + polling ----------
async function refresh() { await loadStats(); if (!$("#more").dataset.paged) await loadCases(); }
$$(".feedbox button").forEach(b => b.onclick = async () => {
  b.disabled = true;
  try { const r = await post(`/api/feed?n=${b.dataset.n}`); toast(`Published ${r.published} payments (${r.mode === "pubsub" ? "Pub/Sub" : "local"})`);
    if (view === "demo") view = "all"; pollUntil = Date.now() + 120000; await refresh(); }
  catch (e) { toast(e.message); }
  b.disabled = false;
});
setInterval(async () => {
  if (busy || !$("#v-work").classList.contains("on")) return;
  if (Date.now() > pollUntil && !(stats && stats.status.INVESTIGATING)) return;
  busy = true;
  try { await refresh(); if (cur && curStatus === "INVESTIGATING") await openCase(cur, true); } catch (e) {}
  busy = false;
}, 3000);

// ---------- other views ----------
$$("nav button").forEach(b => b.onclick = () => { $$("nav button").forEach(x => x.classList.toggle("on", x===b)); $$(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + b.dataset.v)); if (b.dataset.v === "audit") loadAudit(); });
let auditRows = [], auditF = "attention";
const AF = {
  attention: ["Needs attention", a => !(a.action === "SCREENED_AND_ROUTED" && a.to === "RELEASED")],
  auto: ["Auto-cleared (Tier 1)", a => a.action === "SCREENED_AND_ROUTED" && a.to === "RELEASED"],
  ai: ["AI pipeline", a => ["SCREENED_AND_QUEUED", "INVESTIGATED_AND_ROUTED"].includes(a.action)],
  human: ["Customer & analyst actions", a => a.actor !== "system"],
  blocked: ["Blocked", a => a.to === "BLOCKED"],
  all: ["All", a => true],
};
async function loadAudit() {
  const q = $("#audit-q").value.trim();
  $("#audit-out").innerHTML = '<p class="muted"><span class="spin"></span>Loading…</p>';
  auditRows = await api("/api/audit" + (q ? `?txn=${encodeURIComponent(q)}` : ""));
  if (q) auditF = "all";
  drawAudit();
}
function drawAudit() {
  $("#audit-chips").innerHTML = Object.entries(AF).map(([k, [l, f]]) =>
    `<span class="chip ${auditF === k ? "on" : ""}" data-a="${k}">${l} · ${auditRows.filter(f).length}</span>`).join("");
  $$("#audit-chips .chip").forEach(e => e.onclick = () => { auditF = e.dataset.a; drawAudit(); });
  $("#audit-out").innerHTML = auditTable(auditRows.filter(AF[auditF][1]));
  $$("#audit-out [data-open]").forEach(e => e.onclick = () => { $("nav button[data-v=work]").click(); openCase(e.dataset.open); });
}
$("#audit-q").addEventListener("keydown", e => { if (e.key === "Enter") loadAudit(); });
$("#audit-clr").onclick = () => { $("#audit-q").value = ""; auditF = "attention"; loadAudit(); };
$("#audit-ref").onclick = loadAudit;
$("#procall").onclick = async () => { const b = $("#procall"); b.disabled = true; b.textContent = "Screening…";
  try { const r = await post("/api/process_all"); view = "demo"; await refresh(); toast(`${r.processed} demo cases screened and routed`); if (cur) openCase(cur); } catch (e) { toast(e.message); }
  b.disabled = false; b.textContent = "Screen demo cases"; };
$("#reset").onclick = async () => {
  if (!confirm("Reset the demo? Live-feed payments and the audit log are deleted; demo cases go back to Incoming.")) return;
  toast("Resetting…"); const r = await post("/api/reset"); cur = null; view = "demo"; seen = new Set(); await refresh();
  $("#detail").innerHTML = '<div class="empty"><div class="big">◆</div>Demo reset. Select a demo case or send live payments.</div>'; toast(`Demo reset (${r.deleted ?? 0} records removed)`); };
$("#sim-run").onclick = async () => {
  $("#sim-out").innerHTML = '<p class="muted"><span class="spin"></span>Running…</p>';
  const s = await api(`/api/simulate?n=${$("#sim-n").value}&anomaly_pct=${$("#sim-a").value}`);
  const pct = v => Math.max(4, 100 * v / s.screened);
  $("#sim-out").innerHTML = `<div class="funnel">
     <div class="bar" style="width:100%;background:#5f6368">${s.screened} payments · ${s.customers} customers · Tier 1</div>
     <div class="bar" style="width:${pct(s.tier1_clear)}%;background:var(--ok)">${s.tier1_clear} auto-cleared, no AI</div>
     <div class="bar" style="width:${pct(s.to_ai)}%;background:var(--blue)">${s.to_ai} queued for Gemini</div>
     <div class="bar" style="width:${pct(s.floor_human)}%;background:var(--bad)">${s.floor_human} must reach an analyst (mandatory)</div></div>
   <div class="stat"><div><b>${s.no_ai_pct}%</b><small>payments needing no Gemini call</small></div>
     <div><b>${s.tier1_ms_per_payment} ms</b><small>Tier-1 compute per payment (≈${Number(s.tier1_per_sec_one_cpu).toLocaleString("en-IN")}/s on one CPU, excl. I/O)</small></div>
     <div><b>${s.friction_avoided} / ${s.rules_only_friction}</b><small>static-rule frictions removed by behaviour context</small></div></div>
   <p class="muted" style="margin-top:10px">${esc(s.assumptions)} ${s.anomaly_flag_rate}% of injected anomalies were flagged. Normal traffic follows each customer's own patterns, so this shows workload and speed, not real-world detection accuracy.</p>`;
};
$("#bench-run").onclick = async () => {
  $("#bench-out").innerHTML = '<p class="muted"><span class="spin"></span>Running (first run calls Gemini for flagged cases)…</p>';
  try { const b = await api("/api/benchmark");
    $("#bench-out").innerHTML = `<table><tr><th>Case</th><th>Expected</th><th>Rules only</th><th>RiskRoute</th><th>Tier</th><th>Cited / removed</th></tr>
     ${b.rows.map(r => `<tr><td><b>${r.id}</b></td><td>${pill(r.expected, DEC)}</td><td>${pill(r.rules_only, DEC)}</td><td>${pill(r.result, DEC)}</td><td>${r.tier}</td><td>${r.ai_used ? `${r.cited_claims ?? "-"} / ${r.uncited_removed ?? "-"}` : "no AI"}</td></tr>`).join("")}</table>
     <div class="stat"><div><b>${Math.round(b.agreement*100)}%</b><small>RiskRoute matches label</small></div><div><b>${Math.round(b.rules_only_agreement*100)}%</b><small>Rules only matches label</small></div><div><b>${b.mandatory_rule_violations}</b><small>mandatory-rule violations</small></div></div>
     <p class="muted">${esc(b.note)} Gemini calls: ${b.gemini_calls} of ${b.rows.length}.</p>`;
  } catch (e) { $("#bench-out").innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
};
api("/api/health").then(h => { $("#appname").textContent = h.app; document.title = h.app; }).catch(() => {});
refresh();
