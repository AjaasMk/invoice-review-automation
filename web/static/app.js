function activateTab(name) {
  document.querySelectorAll(".tab-button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
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
uploadInput.addEventListener("change", async () => {
  if (!uploadInput.files.length) return;
  await uploadFile(uploadInput.files[0]);
  uploadInput.value = "";
});
uploadBox.addEventListener("dragover", (event) => event.preventDefault());
uploadBox.addEventListener("drop", async (event) => {
  event.preventDefault();
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

async function refreshGate() {
  const response = await fetch("/api/gate/pending");
  const pending = await response.json();
  const container = document.getElementById("gate-list");
  container.innerHTML = "";
  pending.forEach((run) => {
    const card = document.createElement("div");
    card.className = "gate-card";
    const preview = run.triage ? run.triage.preview_text : "no preview available";
    card.innerHTML = `
      <div class="gate-card-header">
        <strong>${run.doc_id}</strong>
        <span class="badge ${run.status}">${run.status}</span>
      </div>
      <p>${preview}</p>
      <div class="gate-card-actions">
        <button class="approve" data-run="${run.run_id}">Approve</button>
        <button class="reject" data-run="${run.run_id}">Reject</button>
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

async function decide(runId, action) {
  await fetch(`/api/gate/${runId}/${action}`, { method: "POST" });
  refreshGate();
  activateTab("live");
  watchRun(runId);
}

async function refreshRunSelect() {
  const response = await fetch("/api/runs");
  const runs = await response.json();
  const select = document.getElementById("run-select");
  select.innerHTML = "";
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

function watchRun(runId) {
  if (activeEventSource) activeEventSource.close();
  const timeline = document.getElementById("stage-timeline");
  timeline.innerHTML = "";
  loadExistingStages(runId);
  activeEventSource = new EventSource(`/api/runs/${runId}/events`);
  activeEventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.stage === "__done__") {
      activeEventSource.close();
      return;
    }
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
      <span>${stage}</span>
      <span>expand</span>
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
  runs.forEach((run) => {
    const row = document.createElement("tr");
    const decision = run.decision;
    const outcome = decision ? decision.outcome : "-";
    const reasons = decision ? decision.reason_codes.join(", ") : "-";
    row.innerHTML = `
      <td>${run.run_id}</td>
      <td><span class="badge ${run.status}">${run.status}</span></td>
      <td>${outcome !== "-" ? `<span class="badge ${outcome}">${outcome}</span>` : "-"}</td>
      <td>${reasons}</td>
      <td>${run.updated_at}</td>
    `;
    row.addEventListener("click", () => {
      activateTab("live");
      watchRun(run.run_id);
    });
    tbody.appendChild(row);
  });
}

refreshGate();
