"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state = {runs: [], pending: [], workspace: null, intake: null, loaded: false, page: "home", filter: "all", query: "", detailId: null, detailVersion: "", stream: null, refreshing: false, motion: false};
const pages = {
  home: ["Home", ""],
  inbox: ["Inbox", "Upload an invoice or receive one from a connected source."],
  invoices: ["Invoices", "Every submission, its outcome, and the evidence behind it."],
  orders: ["Purchase orders", "Live PO balances and approved vendors from the procurement database."],
  review: ["Review queue", "Approve documents for processing and inspect flagged exceptions."],
  reports: ["Reports", "Actual outcomes across all saved submissions."],
  settings: ["Settings", "Runtime configuration, display preferences and development controls."]
};
const paths = {
  home:'<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
  inbox:'<path d="M3 4h18v16H3zM3 13h5l2 3h4l2-3h5"/>',
  invoices:'<path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h6"/>',
  orders:'<path d="M4 6h16v15H4zM8 6V3h8v3M8 11h8M8 15h5"/>',
  review:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m7 12 3 3 7-7"/>',
  reports:'<path d="M4 3v18h17M8 17v-5M13 17V7M18 17v-8"/>',
  settings:'<path d="M4 7h16M4 17h16M8 4v6M16 14v6"/>',
  search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  bell:'<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>'
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
const label = (value) => String(value || "").toLowerCase().replaceAll("_", " ").replace(/\bpo\b/g, "PO").replace(/^./, (c) => c.toUpperCase());
const date = (value) => value ? new Date(value).toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"}) : "—";
function money(value, currency = "USD") {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "—";
  try { return new Intl.NumberFormat(undefined, {style:"currency",currency,minimumFractionDigits:2}).format(Number(value)); }
  catch { return `${currency} ${Number(value).toFixed(2)}`; }
}
function status(run) {
  if (run.status === "ERROR") return ["error", "Processing error", "!"];
  if (run.status === "AWAITING_APPROVAL") return ["pending", "Intake approval", "○"];
  if (run.status === "REJECTED_AT_GATE") return ["rejected", "Intake rejected", "×"];
  const outcomes = {AUTO_APPROVE:["approved","Approved","✓"],NEEDS_REVIEW:["review","Needs review","!"],REJECT:["rejected","Rejected","×"]};
  return outcomes[run.decision?.outcome] || ["running", "Processing", "↻"];
}
function badge(run) { const [kind,text,symbol] = status(run); return `<span class="status ${kind}"><span aria-hidden="true">${symbol}</span>${text}</span>`; }
const resolvedException = (run) => ["CLOSED","ARCHIVED","ON_HOLD"].includes(run.resolution?.status);
const needsAttention = (run) => ["review","pending","error"].includes(status(run)[0]) && !resolvedException(run);
const displayName = (run) => run.invoice?.invoice_number || run.filename || "Unidentified invoice";
const empty = (title, description, uploadLabel = "") => `<div class="empty-state"><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p>${uploadLabel ? `<button class="button primary" data-action="upload">${escapeHTML(uploadLabel)}</button>` : ""}</div>`;
const records = (count) => `${count} record${count === 1 ? "" : "s"}`;
const initials = (vendor) => vendor ? vendor.split(/\s+/).slice(0,2).map(word => word[0]).join("") : "—";
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) { let message = `Request failed (${response.status})`; try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : message; } catch {} throw new Error(message); }
  return response.json();
}
let toastTimer;
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000); }
function renderNavigation() {
  const count = state.runs.filter(needsAttention).length;
  const secondary = ["orders", "reports", "settings"];
  const moreOpen = $("navigation").querySelector("details")?.open || secondary.includes(state.page);
  const link = (key) => {
    const page = pages[key];
    return `<a class="nav-link" href="#${key}" aria-label="${page[0]}" title="${page[0]}" ${state.page === key ? 'aria-current="page"' : ""}>${icon(key)}<span class="nav-text">${page[0]}</span>${key === "review" && count ? `<span class="nav-count" aria-hidden="true">${count}</span>` : ""}</a>`;
  };
  $("navigation").innerHTML = ["home","inbox","review","invoices"].map(link).join("") + `<details class="more-navigation" ${moreOpen ? "open" : ""}><summary aria-label="More tools"><span aria-hidden="true">···</span><span class="nav-text">More</span></summary>${secondary.map(link).join("")}</details>`;
}
function invoiceTable(runs, emptyMessage = "Your invoices will appear here.") {
  if (!runs.length) return empty("Nothing here yet.", emptyMessage);
  return `<div class="table-wrap"><table class="invoice-table"><caption class="sr-only">Invoice submissions and processing outcomes</caption><thead><tr><th>Vendor</th><th>Invoice #</th><th>Amount</th><th>PO #</th><th>Status</th><th>Received</th><th><span class="sr-only">Details</span></th></tr></thead><tbody>${runs.map((run) => {
    const vendor = run.invoice?.vendor_name;
    const duplicate = run.decision?.reason_codes?.includes("EXACT_FILE_DUPLICATE");
    const resolution = run.resolution ? `<small class="resolution-state">${escapeHTML(label(run.resolution.status))}</small>` : "";
    return `<tr class="row-${status(run)[0]}"><td><div class="vendor-cell"><span class="vendor-monogram" aria-hidden="true">${escapeHTML(initials(vendor))}</span><div><strong>${escapeHTML(vendor || (duplicate ? "Identical file" : "Not extracted"))}</strong><small>${escapeHTML(duplicate ? "Duplicate detected" : label(run.source || "upload"))}</small></div></div></td><td><button class="invoice-link" data-run="${escapeHTML(run.run_id)}">${escapeHTML(displayName(run))}</button></td><td class="amount">${escapeHTML(money(run.invoice?.total_amount,run.invoice?.currency))}</td><td>${escapeHTML(run.po_reference || "—")}</td><td>${badge(run)}${resolution}</td><td class="date-cell">${escapeHTML(date(run.created_at))}</td><td><button class="row-open" data-run="${escapeHTML(run.run_id)}" aria-label="View ${escapeHTML(displayName(run))}">›</button></td></tr>`;
  }).join("")}</tbody></table></div>`;
}
function metrics() {
  return [["approved","Approved","✓","Matched and validated"],["review","Needs review","!","Flagged for a closer look"],["rejected","Rejected","×","Includes intake rejections"]].map(([kind,title,symbol,note]) => `<button class="metric" data-metric="${kind}"><span class="metric-label"><span class="status-square ${kind}" aria-hidden="true">${symbol}</span>${title}</span><span class="metric-number">${state.loaded ? state.runs.filter(run => status(run)[0] === kind).length : "—"}</span><span class="metric-note">${note}</span></button>`).join("");
}
function attentionList(attention) {
  if (!attention.length) return state.runs.length ? empty("You’re all caught up.", "Every submission has been processed. New arrivals will appear here.") : empty("No invoices yet.", "Upload an invoice to see Ledger read, match and route it.", "Upload your first invoice");
  return `<div class="attention-list">${attention.slice(0,5).map(run => {
    const kind = status(run)[0];
    const vendor = run.invoice?.vendor_name;
    return `<button class="attention-row row-${kind}" data-run="${escapeHTML(run.run_id)}"><span class="vendor-monogram" aria-hidden="true">${escapeHTML(initials(vendor))}</span><span class="attention-name"><strong>${escapeHTML(vendor || (kind === "pending" ? "Not read yet" : "Vendor not found"))}</strong><small>${escapeHTML(displayName(run))}, received ${escapeHTML(date(run.created_at))}</small></span><span class="amount">${escapeHTML(money(run.invoice?.total_amount,run.invoice?.currency))}</span>${badge(run)}<span class="chevron" aria-hidden="true">›</span></button>`;
  }).join("")}</div>`;
}
function sentence(parts) {
  if (!parts.length) return "";
  const text = parts.length === 1 ? parts[0] : `${parts.slice(0,-1).join(", ")} and ${parts.at(-1)}`;
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}
function filteredRuns(base = state.runs) {
  return base.filter(run => (state.filter === "all" || status(run)[0] === state.filter) && (!state.query || [run.filename,run.invoice?.vendor_name,run.invoice?.invoice_number,run.po_reference,run.run_id].join(" ").toLowerCase().includes(state.query.toLowerCase())));
}
function filterToolbar(review = false) {
  const filters = review ? [["all","All attention"],["pending","Intake approval"],["review","Exceptions"],["error","Errors"]] : [["all","All invoices"],["approved","Approved"],["review","Needs review"],["rejected","Rejected"],["pending","Intake"],["error","Errors"]];
  return `<div class="toolbar"><div class="filter-group" aria-label="Filter invoice status">${filters.map(([key,title]) => `<button class="filter" data-filter="${key}" aria-pressed="${state.filter === key}">${title}</button>`).join("")}</div>${review ? "" : '<button class="button" data-action="export">Export CSV</button>'}</div>${state.query ? `<div class="review-notice">Search results for “${escapeHTML(state.query)}” <button class="filter" data-action="clear-search">Clear search ×</button></div>` : ""}`;
}
function renderNotifications() {
  const attention = state.runs.filter(needsAttention);
  $("notifications").classList.toggle("has-notifications", attention.length > 0);
  $("notification-panel").innerHTML = `<h3>${attention.length} submission${attention.length === 1 ? "" : "s"} to inspect</h3>${attention.slice(0,4).map(run => `<button class="notification-item" data-run="${escapeHTML(run.run_id)}">${escapeHTML(displayName(run))}<small>${escapeHTML(status(run)[1])}</small></button>`).join("")}${attention.length ? '<a class="text-link" href="#review">Open review queue</a>' : '<p>You’re all caught up.</p>'}`;
}
function renderHome() {
  $("home-metrics").innerHTML = metrics();
  if (!state.loaded) return;
  const attention = state.runs.filter(needsAttention);
  const count = (kind) => attention.filter(run => status(run)[0] === kind).length;
  const total = attention.length;
  $("hero-title").textContent = total ? `${total} invoice${total === 1 ? " needs" : "s need"} you.` : "Nothing needs you right now.";
  $("hero-summary").textContent = total
    ? sentence([[count("pending"),"waiting for intake approval"],[count("review"),"flagged for review"],[count("error"),"stopped with an error"]].filter(([n]) => n).map(([n,text]) => `${n} ${text}`))
    : state.runs.length ? `All ${state.runs.length} submissions are processed. Ledger will flag anything that needs your judgment.` : "Ledger reads, matches and routes your invoices, so you only handle the exceptions.";
  $("hero-review").hidden = !total;
  $("attention-all").hidden = total <= 5;
  $("attention-all").textContent = `See all ${total}`;
  $("home-attention").innerHTML = attentionList(attention);
}
function referenceTable(headers, rows) { return `<div class="table-wrap"><table class="reference-table"><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((value,i) => `<td data-label="${headers[i]}">${value}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }
function renderPage() {
  if (state.page === "home") {renderHome();return;}
  const container = $("page-content");
  if (!state.loaded) {container.innerHTML = '<div class="loading-state">Loading your workspace…</div>';return;}
  const ws = state.workspace;
  if (state.page === "invoices" || state.page === "review") {
    const review = state.page === "review";
    container.innerHTML = `${review ? '<p class="review-notice">Intake approval starts extraction; it does not approve payment. Open an exception to record a resolution, create missing reference data, or archive it with an audit trail.</p>' : ""}${filterToolbar(review)}${invoiceTable(filteredRuns(review ? state.runs.filter(needsAttention) : state.runs), "No submissions match this view. Try another filter.")}`;
  } else if (state.page === "inbox") {
    const intake = state.intake || {};
    const intakeNotice = intake.enabled ? `<div class="review-notice">${intake.last_error ? `Mailbox check needs attention: ${escapeHTML(intake.last_error)}` : `${intake.unclassified_unread_attachments || 0} unread attachment email(s) awaiting classification · ${intake.awaiting_human_triage || 0} email(s) held unread for human triage.`}${intake.last_poll_at ? `<small> Last checked ${escapeHTML(new Date(intake.last_poll_at).toLocaleTimeString())}.</small>` : " Waiting for first mailbox check."}</div>` : "";
    container.innerHTML = `${intakeNotice}<div class="split-grid"><div class="intake-panel"><h2>Upload an invoice</h2><p>PDFs and scanned invoices up to 15 MB. Exact duplicate files are caught before any AI processing.</p><button class="button primary" data-action="upload">Choose invoice</button></div><div class="panel"><h2>Connected sources</h2><div class="source-list"><div class="source-row"><span>Manual upload<small>PDF, PNG, JPG · 15 MB maximum</small></span><span class="status approved">✓ Ready</span></div><div class="source-row"><span>Watched folder<small>runs/inbox</small></span><span class="status approved">✓ Configured</span></div><div class="source-row"><span>Gmail / IMAP<small>${ws.runtime.email_enabled ? "Unread attachment monitoring enabled" : "Connect mailbox credentials to enable"}</small></span><span class="status ${ws.runtime.email_enabled ? "approved" : ""}">${ws.runtime.email_enabled ? "✓ Configured" : "Not connected"}</span></div></div></div></div><div class="section-heading"><h2>Recent arrivals</h2><a class="text-link" href="#review">Review intake</a></div>${invoiceTable(state.runs.slice(0,10))}`;
  } else if (state.page === "orders") {
    const vendors = Object.fromEntries(ws.vendors.map(v => [v.vendor_id,v.canonical_name]));
    container.innerHTML = `<div class="section-heading"><h2>Purchase orders <span class="inline-count">${records(ws.purchase_orders.length)}</span></h2></div>${referenceTable(["PO number","Vendor","PO amount","Invoiced","Remaining","Utilization"],ws.purchase_orders.map(po => [escapeHTML(po.po_id),escapeHTML(vendors[po.vendor_id] || po.vendor_id),escapeHTML(money(po.amount,po.currency)),escapeHTML(money(po.invoiced_to_date,po.currency)),escapeHTML(money(po.remaining_balance,po.currency)),`${Number(po.amount) ? Math.round(Number(po.invoiced_to_date)/Number(po.amount)*100) : 0}%<div class="progress-track"><span style="width:${Math.max(0,Math.min(100,Number(po.amount) ? Number(po.invoiced_to_date)/Number(po.amount)*100 : 0))}%"></span></div>`]))}<div class="section-heading" style="margin-top:40px"><h2>Vendor directory <span class="inline-count">${records(ws.vendors.length)}</span></h2></div>${referenceTable(["Vendor","Reference","Recognized aliases","Approval"],ws.vendors.map(v => [escapeHTML(v.canonical_name),escapeHTML(v.vendor_id),escapeHTML(v.aliases.join(", ") || "—"),`<span class="status ${v.approved ? "approved" : "rejected"}">${v.approved ? "✓ Approved" : "× Not approved"}</span>`]))}`;
  } else if (state.page === "reports") {
    const reasons = {}, sources = {};
    state.runs.forEach(run => { (run.decision?.reason_codes || []).forEach(reason => {reasons[reason] = (reasons[reason] || 0)+1;}); sources[run.source || "unknown"] = (sources[run.source || "unknown"] || 0)+1; });
    const breakdown = Object.entries(reasons).sort((a,b) => b[1]-a[1]);
    container.innerHTML = `<div class="metrics">${metrics()}</div><p class="report-caption">${state.runs.length} total submissions · ${state.runs.filter(r => r.status === "AWAITING_APPROVAL").length} awaiting intake approval · ${state.runs.filter(r => r.status === "ERROR").length} processing errors. These are submissions, not unique invoices.</p><div class="report-grid"><section><h2>Decision reasons</h2>${breakdown.length ? breakdown.map(([reason,count]) => `<div class="report-row"><span>${escapeHTML(label(reason))}</span><strong>${count}</strong></div>`).join("") : '<p class="report-caption">Decision reasons appear after processing.</p>'}<p class="report-caption">One submission can have more than one reason.</p></section><section><h2>Intake by source</h2>${Object.entries(sources).map(([source,count]) => `<div class="report-row"><span>${escapeHTML(label(source))}</span><strong>${count}</strong></div>`).join("") || '<p class="report-caption">No submissions yet.</p>'}<h2 style="margin-top:35px">Processing safeguards</h2><div class="report-row"><span>Exact-file duplicate detection</span><strong>Enabled</strong></div><div class="report-row"><span>Human intake approval</span><strong>Required</strong></div><div class="report-row"><span>PO balance checks</span><strong>Enabled</strong></div></section></div>`;
  } else if (state.page === "settings") {
    container.innerHTML = `<section class="settings-section"><h2>Processing configuration</h2>${info("Extraction provider", ws.runtime.extraction_client.replace("ExtractionClient", ""))}${info("Model",ws.runtime.model || "Test / custom client")}${info("Email ingestion",ws.runtime.email_enabled ? "IMAP configured" : "Not connected")}${info("Storage","SQLite · local workspace")}${info("Approval policy","Human intake gate before extraction")}<p style="margin-top:15px">Provider and mailbox credentials are configured in the server’s .env file. Secrets are never displayed here.</p></section><section class="settings-section"><h2>Display & motion</h2><p>Keep the workflow illustration static. Your device’s reduced-motion preference is always respected.</p><label class="setting-choice"><input type="checkbox" id="reduce-motion" ${state.motion ? "checked" : ""}>Reduce motion in this browser</label></section><section class="settings-section danger-zone"><h2>Reset development data</h2><p>Start the assessment demo again with empty history and restored PO balances. This is a local development control, not a production administration feature.</p><div class="button-row"><button class="button" data-action="reset">Reset demo data…</button></div></section>`;
  }
}
const info = (key,value) => `<div class="info-line"><span>${escapeHTML(key)}</span><strong>${escapeHTML(value ?? "—")}</strong></div>`;
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [runs,pending,workspace,intake] = await Promise.all([api("/api/runs"),api("/api/gate/pending"),api("/api/workspace"),api("/api/intake/status")]);
    const changed = JSON.stringify([runs,pending,workspace,intake]) !== JSON.stringify([state.runs,state.pending,state.workspace,state.intake]);
    state.runs = runs; state.pending = pending; state.workspace = workspace; state.intake = intake; state.loaded = true;
    $("connection-state").textContent = "Workspace connected"; $("connection-error").hidden = true;
    if (changed) {renderNavigation();renderPage();renderNotifications();}
    if (state.detailId && $("detail-dialog").open) await loadDetail(false);
  } catch (error) {
    $("connection-state").textContent = "Connection interrupted";
    $("connection-error").innerHTML = location.protocol === "file:"
      ? "This page was opened as a file, so it can’t reach the workspace. Run <code>python -m web.server</code> and open http://localhost:8000."
      : `Could not refresh the workspace. ${state.loaded ? "Showing the last saved view." : "Check that the server is running."} <button data-action="refresh">Retry</button>`;
    $("connection-error").hidden = false;
  } finally { state.refreshing = false; }
}
function navigate() {
  const route = location.hash.slice(1).split("?")[0];
  if (route === "main") return;
  if ($("workflow-dialog").open) $("workflow-dialog").close();
  const previous = state.page;
  state.page = pages[route] ? route : "home";
  if (previous !== state.page) {state.filter = "all"; if (state.page !== "invoices") {state.query = "";$("global-search").value = "";}}
  $("home-view").hidden = state.page !== "home"; $("page-view").hidden = state.page === "home";
  $("breadcrumb").textContent = state.page === "home" ? "Overview" : pages[state.page][0];
  $("page-title").textContent = pages[state.page][0]; $("page-description").textContent = pages[state.page][1];
  document.title = state.page === "home" ? "Ledger" : `${pages[state.page][0]} | Ledger`;
  renderNavigation(); renderPage(); closePopovers(); setNav(false);
  if (previous !== state.page) {window.scrollTo(0,0);$("main").focus({preventScroll:true});}
}
function closePopovers() {for (const [button,panel] of [["notifications","notification-panel"],["account","account-panel"]]) {$(panel).hidden = true;$(button).setAttribute("aria-expanded","false");}}
function setNav(open) {
  document.body.classList.toggle("nav-open",open); $("nav-scrim").hidden = !open;
  $("menu-toggle").setAttribute("aria-expanded",String(open)); $("menu-toggle").setAttribute("aria-label",open ? "Close navigation" : "Open navigation");
  $("sidebar").inert = window.matchMedia("(max-width:600px)").matches && !open;
}
async function openDetail(runId) {
  closePopovers(); state.stream?.close(); state.stream = null; state.detailId = runId; state.detailVersion = "";
  $("detail-content").innerHTML = '<h2 id="detail-title">Invoice details</h2><p>Loading evidence…</p>';
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
  await loadDetail(true);
}
async function loadDetail(watch) {
  const id = state.detailId;
  if (!id) return;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (state.detailId !== id || !$("detail-dialog").open) return;
    const version = JSON.stringify(run);
    if (version !== state.detailVersion) { renderDetail(run, !state.detailVersion); state.detailVersion = version; }
    if (watch && ["RUNNING","AWAITING_APPROVAL"].includes(run.status)) {
      state.stream?.close(); const stream = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`); state.stream = stream;
      stream.onmessage = (event) => {let data;try {data = JSON.parse(event.data);}catch{return;}loadDetail(false);if(data.stage === "__done__") {stream.close();refresh();}};
      stream.onerror = () => {stream.close();};
    }
  } catch(error) { if(state.detailId === id) $("detail-content").innerHTML = `<h2 id="detail-title">Unable to load invoice</h2><p>${escapeHTML(error.message)}</p><button class="button" data-run="${escapeHTML(id)}">Try again</button>`; }
}
function verdict(run, stamp) {
  const [kind,text,symbol] = status(run);
  const notes = {pending:"Approve it to start extraction, or reject it.",running:"Ledger is reading and matching this invoice.",error:"Processing stopped before a decision was made."};
  const note = (run.decision?.reason_codes || []).map(label).join(", ") || notes[kind] || "";
  return `<div class="verdict ${kind}${stamp ? " stamp" : ""}"><span class="verdict-symbol" aria-hidden="true">${symbol}</span><div><strong>${escapeHTML(text)}</strong>${note ? `<p>${escapeHTML(note)}</p>` : ""}</div></div>`;
}
function exceptionActions(run) {
  if (run.decision?.outcome !== "NEEDS_REVIEW" || resolvedException(run)) return "";
  const reasons = new Set(run.decision.reason_codes || []);
  const actions = [];
  const add = (action, title, primary = false) => actions.push(`<button class="button${primary ? " primary" : ""}" data-exception="${action}" data-id="${escapeHTML(run.run_id)}">${title}</button>`);
  if (reasons.has("VENDOR_NOT_FOUND")) add("add_vendor", "Add vendor", true);
  if (reasons.has("PO_NOT_FOUND")) { add("select_purchase_order", "Match to PO", true); add("create_purchase_order", "Create PO"); }
  if (["DUPLICATE_SUSPECTED", "EXACT_FILE_DUPLICATE"].some(reason => reasons.has(reason))) { add("confirm_duplicate", "Confirm duplicate", true); add("mark_distinct", "Mark distinct"); }
  if (["LOW_EXTRACTION_CONFIDENCE", "MISSING_REQUIRED_FIELD"].some(reason => reasons.has(reason))) add("correct_fields", "Correct fields", true);
  if (["AMOUNT_OVER_TOLERANCE", "PO_BALANCE_EXCEEDED", "CONCURRENT_PO_UPDATE"].some(reason => reasons.has(reason))) { add("approve_exception", "Approve exception", true); add("hold_for_procurement", "Hold for procurement"); }
  if (["REFERENCE_ADDED", "MATCH_ASSIGNED", "CORRECTION_RECORDED", "OPEN"].includes(run.resolution?.status)) add("recheck_invoice", "Re-check invoice", true);
  add("reject_invoice", "Reject invoice");
  add("archive_invoice", "Archive");
  return `<section class="exception-actions"><h3>Resolve exception</h3><p>Choose an action. Ledger records the original automated finding and your human resolution.</p><div class="drawer-actions">${actions.join("")}</div></section>`;
}
function resolutionSummary(run) {
  if (!run.resolution) return "";
  return `<div class="decision-note resolution-summary"><strong>${escapeHTML(label(run.resolution.status))}</strong><p>${escapeHTML(label(run.resolution.action))}${run.resolution.note ? ` — ${escapeHTML(run.resolution.note)}` : ""}</p><small>Recorded ${escapeHTML(date(run.resolution.resolved_at))}</small></div>`;
}
function renderDetail(run, stamp = false) {
  const opened = new Set([...$("detail-content").querySelectorAll("details[open]")].map(el => el.dataset.stage));
  const stages = run.stages || [];
  const stage = name => [...stages].reverse().find(item => item.stage === name)?.payload;
  const triage = stage("triage"), duplicate = stage("deduplicate"), match = stage("po_match"), invoice = stage("validate");
  const title = displayName(run);
  const error = stage("error");
  $("detail-content").innerHTML = `${verdict(run, stamp)}<h2 id="detail-title">${escapeHTML(title)}</h2><p class="detail-subtitle">${escapeHTML(run.invoice?.vendor_name || run.filename || "No vendor extracted")}</p>${run.invoice?.total_amount != null ? `<div class="detail-amount">${escapeHTML(money(run.invoice.total_amount,run.invoice.currency))}</div>` : '<div class="detail-spacer"></div>'}${info("Purchase order",run.po_reference)}${info("Received",date(run.created_at))}${info("Source",label(run.source || "upload"))}${info("Run reference",run.run_id)}
    ${run.status === "AWAITING_APPROVAL" ? `<div class="decision-note"><strong>Ready for intake review</strong><p>${escapeHTML(triage?.preview_text || "Review the document before starting extraction.")}</p><p>${escapeHTML(triage?.reason || "")}</p></div><div class="drawer-actions"><button class="button primary" data-gate="approve" data-id="${escapeHTML(run.run_id)}">Approve for processing</button><button class="button" data-gate="reject" data-id="${escapeHTML(run.run_id)}">Reject intake</button></div>` : ""}
    ${run.decision ? `<div class="decision-note"><strong>Why this outcome?</strong><p>${escapeHTML(run.decision.explanation)}</p><ul class="reason-list">${(run.decision.reason_codes || []).map(reason => `<li>${escapeHTML(label(reason))}</li>`).join("")}</ul></div>` : ""}
    ${resolutionSummary(run)}${exceptionActions(run)}
    ${duplicate?.original_run_id ? `<button class="button" data-run="${escapeHTML(duplicate.original_run_id)}">View original submission</button>` : ""}
    ${error ? `<div class="decision-note"><strong>Processing stopped</strong><p>${escapeHTML(error.message || error.error_type)}</p></div>` : ""}
    ${match?.po ? `<h3>Purchase order comparison</h3>${info("Matched PO", match.po.po_id)}${info("Invoice total",money(invoice?.total_amount,invoice?.currency))}${info("PO amount",money(match.po.amount,match.po.currency))}${info("Remaining at matching",money(match.remaining_po_balance,match.po.currency))}<p>These are the values captured during processing, not the current PO balance.</p>` : ""}
    <h3>Processing evidence <span class="inline-count">${stages.length} events</span></h3><p style="margin-bottom:15px">Expand any stage to inspect the exact recorded result.</p>${stages.map((entry,i) => `<details data-stage="${i}" ${opened.has(String(i)) ? "open" : ""}><summary>${escapeHTML(label(entry.stage))}<small>${escapeHTML(entry.created_at ? new Date(entry.created_at).toLocaleTimeString() : "")}</small></summary><pre>${escapeHTML(JSON.stringify(entry.payload,null,2))}</pre></details>`).join("")}`;
}
async function gateDecision(button) {
  const id = button.dataset.id, decision = button.dataset.gate;
  const actions = $("detail-content").querySelectorAll("[data-gate]"); actions.forEach(el => {el.disabled = true;});
  try { await api(`/api/gate/${encodeURIComponent(id)}/${decision}`,{method:"POST"}); toast(decision === "approve" ? "Approved for processing. Ledger is working on it." : "Intake rejection requested."); await loadDetail(true); await refresh(); }
  catch(error) {toast(error.message);actions.forEach(el => {el.disabled = false;});}
}
function exportCSV() {
  const cell = (value) => {let text = String(value ?? "");if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;return '"'+text.replaceAll('"','""')+'"';};
  const rows = [["Vendor","Invoice","Amount","Currency","PO","Status","Received","Run"],...filteredRuns().map(r => [r.invoice?.vendor_name,displayName(r),r.invoice?.total_amount,r.invoice?.currency,r.po_reference,status(r)[1],r.created_at,r.run_id])];
  const url = URL.createObjectURL(new Blob(["\uFEFF"+rows.map(row => row.map(cell).join(",")).join("\r\n")],{type:"text/csv;charset=utf-8"}));
  const link = document.createElement("a");link.href = url;link.download = "ledger-invoices.csv";link.click();setTimeout(() => URL.revokeObjectURL(url),1000);toast(`Exported ${rows.length-1} submissions.`);
}
function updateMotion() {
  document.body.classList.toggle("motion-static",state.motion);
}
function setFile(file) {
  if (!file) return false;
  if (!/\.(pdf|png|jpe?g)$/i.test(file.name) || file.size === 0 || file.size > 15*1024*1024) {$("upload-message").textContent = "Choose a non-empty PDF, PNG or JPG smaller than 15 MB.";$("invoice-file").value = "";$("file-label").textContent = "Choose a file or drop it here";return false;}
  $("file-label").textContent = file.name;$("upload-message").textContent = "Ready to upload.";return true;
}
let exceptionContext = null;
function optionList(items, value, text, selected = "") { return items.map(item => `<option value="${escapeHTML(item[value])}" ${item[value] === selected ? "selected" : ""}>${escapeHTML(item[text])}</option>`).join(""); }
function openExceptionDialog(runId, action) {
  const run = state.runs.find(item => item.run_id === runId);
  if (!run) return;
  exceptionContext = {runId, action};
  const form = $("exception-form"), invoice = run.invoice || {}, vendors = state.workspace?.vendors || [], orders = state.workspace?.purchase_orders || [];
  const title = label(action);
  $("exception-title").textContent = title;
  $("exception-description").textContent = `Record a human resolution for ${displayName(run)}. The automated result remains in the audit trail.`;
  let fields = "";
  if (action === "add_vendor") fields = `<label>Vendor name<input name="vendor_name" required value="${escapeHTML(invoice.vendor_name || "")}"></label><label>Aliases <input name="vendor_aliases" placeholder="Comma-separated aliases"></label><label class="check-field"><input type="checkbox" name="vendor_approved"> Add as an approved vendor</label>`;
  if (action === "create_purchase_order") fields = `<label>Vendor<select name="selected_vendor_id" required><option value="">Select vendor</option>${optionList(vendors,"vendor_id","canonical_name")}</select></label><label>PO number<input name="po_id" required placeholder="PO-1021"></label><label>PO amount<input name="po_amount" required type="number" min="0.01" step="0.01" value="${escapeHTML(invoice.total_amount || "")}"></label><label>Issued date<input name="po_issued_date" required type="date"></label><label>Tax treatment<select name="po_tax_treatment"><option value="exclusive">Tax exclusive</option><option value="inclusive">Tax inclusive</option></select></label>`;
  if (action === "select_purchase_order") fields = `<label>Purchase order<select name="selected_po_id" required><option value="">Select PO</option>${optionList(orders,"po_id","po_id",invoice.po_reference || "")}</select></label>`;
  if (action === "correct_fields") fields = `<label>Vendor name<input name="correct_vendor_name" value="${escapeHTML(invoice.vendor_name || "")}"></label><label>Invoice number<input name="correct_invoice_number" value="${escapeHTML(invoice.invoice_number || "")}"></label><label>Total amount<input name="correct_total_amount" type="number" min="0" step="0.01" value="${escapeHTML(invoice.total_amount || "")}"></label><label>PO reference<input name="correct_po_reference" value="${escapeHTML(invoice.po_reference || "")}"></label>`;
  const noteRequired = ["approve_exception","reject_invoice","archive_invoice","hold_for_procurement","confirm_duplicate","mark_distinct"].includes(action);
  form.innerHTML = `${fields}<label>Resolution note<textarea name="note" ${noteRequired ? "required" : ""} placeholder="Why is this the correct action?"></textarea></label><p class="form-message" id="exception-message" role="status"></p><div class="button-row dialog-actions"><button class="button" type="button" data-close="exception-dialog">Cancel</button><button class="button primary" type="submit">Record resolution</button></div>`;
  $("exception-dialog").showModal();
}
$("exception-form").addEventListener("submit", async event => {
  event.preventDefault(); if (!exceptionContext) return;
  const form = event.currentTarget, submit = form.querySelector('[type="submit"]'), data = Object.fromEntries(new FormData(form));
  data.action = exceptionContext.action;
  if (data.vendor_aliases) data.vendor_aliases = data.vendor_aliases.split(",").map(value => value.trim()).filter(Boolean);
  data.vendor_approved = form.querySelector('[name="vendor_approved"]')?.checked || false;
  if (data.po_amount) data.po_amount = Number(data.po_amount);
  const corrected = Object.fromEntries(Object.entries(data).filter(([key, value]) => key.startsWith("correct_") && value !== "").map(([key, value]) => [key.replace("correct_", ""), value]));
  Object.keys(data).filter(key => key.startsWith("correct_")).forEach(key => delete data[key]);
  if (Object.keys(corrected).length) data.corrected_fields = corrected;
  submit.disabled = true; $("exception-message").textContent = "Recording resolution…";
  try { await api(`/api/runs/${encodeURIComponent(exceptionContext.runId)}/exception-resolution`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}); $("exception-dialog").close(); toast("Resolution recorded in the audit trail."); await refresh(); await loadDetail(false); }
  catch (error) { $("exception-message").textContent = error.message; submit.disabled = false; }
});
document.addEventListener("click", (event) => {
  const target = event.target.closest("button,a"); if (!target) return;
  if (target.dataset.close) $(target.dataset.close).close();
  if (target.dataset.run) openDetail(target.dataset.run);
  if (target.dataset.gate) gateDecision(target);
  if (target.dataset.exception) openExceptionDialog(target.dataset.id, target.dataset.exception);
  if (target.dataset.filter) {state.filter = target.dataset.filter;renderPage();}
  if (target.dataset.metric) {const filter = target.dataset.metric;location.hash = "invoices";navigate();state.filter = filter;renderPage();}
  if (target.dataset.action === "upload") {closePopovers();$("upload-dialog").showModal();}
  if (target.dataset.action === "workflow") {
    $("workflow-dialog").showModal();
    if (!state.motion && !window.matchMedia("(prefers-reduced-motion:reduce)").matches) {
      $("workflow").getAnimations({subtree:true}).forEach(animation => {animation.cancel();animation.play();});
    }
  }
  if (target.dataset.action === "reset") $("reset-dialog").showModal();
  if (target.dataset.action === "refresh") refresh();
  if (target.dataset.action === "export") exportCSV();
  if (target.dataset.action === "clear-search") {state.query = "";$("global-search").value = "";renderPage();}
});
$("search-form").addEventListener("submit", event => {event.preventDefault();state.query = $("global-search").value.trim();state.filter = "all";location.hash = "invoices";navigate();});
$("invoice-file").addEventListener("change", () => setFile($("invoice-file").files[0]));
for (const name of ["dragenter","dragover"]) $("dropzone").addEventListener(name,event => {event.preventDefault();$("dropzone").classList.add("dragging");});
for (const name of ["dragleave","drop"]) $("dropzone").addEventListener(name,event => {event.preventDefault();$("dropzone").classList.remove("dragging");});
$("dropzone").addEventListener("drop",event => {if($("upload-submit").disabled)return;const files=event.dataTransfer.files;if(files.length !== 1){$("upload-message").textContent="Please upload one invoice at a time.";return;}if(setFile(files[0]))$("invoice-file").files=files;});
$("upload-form").addEventListener("submit",async event => {
  event.preventDefault();const file=$("invoice-file").files[0];if(!setFile(file)||$("upload-submit").disabled)return;
  const data=new FormData();data.append("file",file);$("upload-submit").disabled=true;$("invoice-file").disabled=true;$("upload-submit").textContent="Checking invoice…";$("upload-message").textContent="Checking duplicates and preparing intake review. This may take a minute.";
  try {const result=await api("/api/upload",{method:"POST",body:data});$("upload-dialog").close();$("upload-form").reset();$("file-label").textContent="Choose a file or drop it here";$("upload-message").textContent="";await refresh();await openDetail(result.run_id);}
  catch(error){$("upload-message").textContent=error.message;}
  finally{$("upload-submit").disabled=false;$("invoice-file").disabled=false;$("upload-submit").textContent="Upload and check";}
});
$("confirm-reset").addEventListener("click",async () => {
  $("confirm-reset").disabled=true;$("reset-message").textContent="Resetting development data…";
  try {const token = $("reset-token").value;await api("/api/demo/reset?confirm=true",{method:"POST",headers:token ? {"X-Demo-Reset-Token":token} : {}});$("reset-dialog").close();$("detail-dialog").close();state.detailId=null;state.stream?.close();$("reset-token").value="";await refresh();toast("Demo history cleared and PO balances reset.");$("reset-message").textContent="";}
  catch(error){$("reset-message").textContent=error.message;}
  finally{$("confirm-reset").disabled=false;}
});
$("detail-dialog").addEventListener("close",()=>{state.stream?.close();state.stream=null;state.detailId=null;});
for (const [button,panel] of [["notifications","notification-panel"],["account","account-panel"]]) $(button).addEventListener("click",()=>{const open=$(panel).hidden;closePopovers();$(panel).hidden=!open;$(button).setAttribute("aria-expanded",String(open));});
document.addEventListener("click",event=>{if(!event.target.closest(".popover,#notifications,#account"))closePopovers();});
$("menu-toggle").addEventListener("click",()=>{const open=!document.body.classList.contains("nav-open");setNav(open);if(open)$("navigation").querySelector("a").focus();});
$("nav-scrim").addEventListener("click",()=>{setNav(false);$("menu-toggle").focus();});
document.addEventListener("keydown",event=>{if(event.key==="Escape"){const navOpen=document.body.classList.contains("nav-open");setNav(false);closePopovers();if(navOpen)$("menu-toggle").focus();}if(event.key==="Tab"&&document.body.classList.contains("nav-open")){const links=[...$("sidebar").querySelectorAll("a")];if(event.shiftKey&&document.activeElement===links[0]){event.preventDefault();links.at(-1).focus();}else if(!event.shiftKey&&document.activeElement===links.at(-1)){event.preventDefault();links[0].focus();}}});
document.addEventListener("change",event=>{if(event.target.id==="reduce-motion"){state.motion=event.target.checked;try{localStorage.setItem("ledger-reduced-motion",String(state.motion));}catch{}updateMotion();}});
window.addEventListener("hashchange",navigate);
window.matchMedia("(max-width:600px)").addEventListener("change",()=>setNav(false));
window.matchMedia("(prefers-reduced-motion:reduce)").addEventListener("change",updateMotion);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
$("search-icon").innerHTML=icon("search");$("notifications").innerHTML=icon("bell");$("menu-toggle").innerHTML=icon("menu");
$("today").textContent=new Date().toLocaleDateString(undefined,{weekday:"long",day:"numeric",month:"long"});
try{state.motion=localStorage.getItem("ledger-reduced-motion")==="true";}catch{}
updateMotion();navigate();refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
