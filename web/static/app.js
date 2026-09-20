function activateTab(name) {
  document.querySelectorAll(".tab-button").forEach((btn) => {
    const isActive = btn.dataset.tab === name;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${name}`);
  });
  if (name === "dashboard") refreshDashboard();
  if (name === "gate") refreshGate();
  if (name === "live") refreshRunSelect();
}

document.querySelectorAll(".tab-button").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

const uploadBox = document.querySelector(".upload-box");
const uploadInput = document.getElementById("upload-input");

uploadBox.addEventListener("click", () => uploadInput.click());
uploadBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") uploadInput.click();
});
uploadInput.addEventListener("change", async () => {
  if (!uploadInput.files.length) return;
  await uploadFile(uploadInput.files[0]);
  uploadInput.value = "";
});
["dragenter", "dragover"].forEach((eventName) => {
  uploadBox.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadBox.classList.add("drag-active");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  uploadBox.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadBox.classList.remove("drag-active");
  });
});
uploadBox.addEventListener("drop", async (event) => {
  if (event.dataTransfer.files.length) {
    await uploadFile(event.dataTransfer.files[0]);
  }
});

async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  await fetch("/api/upload", { method: "POST", body: formData });
  refreshGate();
}

function toggleEmpty(emptyId, isEmpty) {
  document.getElementById(emptyId).classList.toggle("visible", isEmpty);
}

function reasonPills(reasonCodes) {
  if (!reasonCodes || !reasonCodes.length) {
    return '<span class="reason-pill">clean match</span>';
  }
  return `<div class="reason-pill-list">${reasonCodes
    .map((code) => `<span class="reason-pill">${code.replace(/_/g, " ").toLowerCase()}</span>`)
    .join("")}</div>`;
}

async function refreshGate() {
  const response = await fetch("/api/gate/pending");
  const pending = await response.json();
  const container = document.getElementById("gate-list");
  container.innerHTML = "";
  toggleEmpty("gate-empty", pending.length === 0);

  pending.forEach((run) => {
    const card = document.createElement("div");
    card.className = "gate-card";
    const preview = run.triage ? run.triage.preview_text : "No preview available for this document.";
    card.innerHTML = `
      <div class="gate-card-header">
        <span class="gate-card-id">${run.doc_id}</span>
        <span class="stamp ${run.status}">${run.status.replace(/_/g, " ")}</span>
      </div>
      <p class="gate-card-preview">&ldquo;${preview}&rdquo;</p>
      <div class="gate-card-actions">
        <button class="stamp-btn approve" data-run="${run.run_id}">Approve</button>
        <button class="stamp-btn reject" data-run="${run.run_id}">Reject</button>
      </div>
    `;
    container.appendChild(card);
  });

  container.querySelectorAll("button.approve").forEach((btn) => {
    btn.addEventListener("click", () => decide(btn.dataset.run, "approve"));
  });
  container.querySelectorAll("button.reject").forEach((btn) => {
    btn.addEventListener("click", () => decide(btn.dataset.run, "reject"));
  });
}

function setStatus(active, text) {
  const chip = document.getElementById("status-chip");
  chip.classList.toggle("active", active);
  document.getElementById("status-text").textContent = text;
}

async function decide(runId, action) {
  await fetch(`/api/gate/${runId}/${action}`, { method: "POST" });
  refreshGate();
  activateTab("live");
  if (action === "approve") {
    setStatus(true, `Ledger is reviewing ${runId}`);
  } else {
    setStatus(false, "Ledger is idle");
  }
  watchRun(runId);
}

async function refreshRunSelect() {
  const response = await fetch("/api/runs");
  const runs = await response.json();
  const select = document.getElementById("run-select");
  select.innerHTML = "";
  toggleEmpty("live-empty", runs.length === 0);
  document.getElementById("stage-timeline").style.display = runs.length === 0 ? "none" : "flex";
  document.querySelector(".live-controls").style.display = runs.length === 0 ? "none" : "flex";

  runs.forEach((run) => {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.run_id} — ${run.status}`;
    select.appendChild(option);
  });
  select.onchange = () => watchRun(select.value);
  if (runs.length) watchRun(runs[0].run_id);
}

let activeEventSource = null;

function setLiveIndicator(connected) {
  document.getElementById("live-indicator").classList.toggle("connected", connected);
}

function watchRun(runId) {
  if (activeEventSource) activeEventSource.close();
  const timeline = document.getElementById("stage-timeline");
  timeline.innerHTML = "";
  loadExistingStages(runId);

  activeEventSource = new EventSource(`/api/runs/${runId}/events`);
  activeEventSource.onopen = () => setLiveIndicator(true);
  activeEventSource.onerror = () => setLiveIndicator(false);
  activeEventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.stage === "__done__") {
      setLiveIndicator(false);
      setStatus(false, "Ledger is idle");
      activeEventSource.close();
      return;
    }
    setStatus(true, `Ledger is on ${data.stage.replace(/_/g, " ")}`);
    appendStage(data.stage, data.payload);
  };
}

async function loadExistingStages(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  const run = await response.json();
  run.stages.forEach((stage) => appendStage(stage.stage, stage.payload));
}

function appendStage(stage, payload) {
  const timeline = document.getElementById("stage-timeline");
  const item = document.createElement("li");
  item.className = "stage-item";
  item.innerHTML = `
    <div class="stage-item-header">
      <span>${stage.replace(/_/g, " ")}</span>
      <span class="stage-item-marker">&rsaquo;</span>
    </div>
    <pre class="stage-payload">${JSON.stringify(payload, null, 2)}</pre>
  `;
  item.addEventListener("click", () => item.classList.toggle("expanded"));
  timeline.appendChild(item);
}

async function refreshDashboard() {
  const response = await fetch("/api/runs");
  const runs = await response.json();
  const tbody = document.querySelector("#runs-table tbody");
  tbody.innerHTML = "";
  toggleEmpty("dashboard-empty", runs.length === 0);
  document.getElementById("runs-table").style.display = runs.length === 0 ? "none" : "table";

  runs.forEach((run) => {
    const row = document.createElement("tr");
    const decision = run.decision;
    const outcome = decision ? decision.outcome : null;
    row.innerHTML = `
      <td class="run-id-cell">${run.run_id}</td>
      <td><span class="stamp ${run.status}">${run.status.replace(/_/g, " ")}</span></td>
      <td>${outcome ? `<span class="stamp ${outcome}">${outcome.replace(/_/g, " ")}</span>` : "&mdash;"}</td>
      <td>${decision ? reasonPills(decision.reason_codes) : "&mdash;"}</td>
      <td class="updated-cell">${run.updated_at}</td>
    `;
    row.addEventListener("click", () => {
      activateTab("live");
      watchRun(run.run_id);
    });
    tbody.appendChild(row);
  });
}

refreshGate();
