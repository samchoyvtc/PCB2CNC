import { setupDropzone, uploadZip } from "./upload.js";
import { BoardPreview, renderLayerToggles, renderBomTable } from "./preview.js";
import { readSettings } from "./settings.js";
import { generateJob, renderDownloads, showToolpathPreview } from "./output.js";

const statusEl = document.getElementById("status");
const fileListEl = document.getElementById("file-list");
const layerTogglesEl = document.getElementById("layer-toggles");
const bomPanelEl = document.getElementById("bom-panel");
const downloadsEl = document.getElementById("downloads");
const toolpathImg = document.getElementById("toolpath-preview");
const btnGenerate = document.getElementById("btn-generate");
const btnGenerateMachine = document.getElementById("btn-generate-machine");
const settingsForm = document.getElementById("settings-form");
const tabLayers = document.getElementById("tab-layers");
const tabMachine = document.getElementById("tab-machine");
const panelLayers = document.getElementById("panel-layers");
const panelMachine = document.getElementById("panel-machine");

const pills = {
  1: document.getElementById("pill-1"),
  2: document.getElementById("pill-2"),
  3: document.getElementById("pill-3"),
  4: document.getElementById("pill-4"),
};

let jobId = null;
let currentStage = 1;
const board = new BoardPreview(document.getElementById("board-canvas"));

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function setGenerateEnabled(enabled) {
  btnGenerate.disabled = !enabled;
  btnGenerateMachine.disabled = !enabled;
}

function setStage(n) {
  currentStage = n;
  Object.entries(pills).forEach(([k, el]) => {
    const stage = Number(k);
    el.classList.toggle("active", stage <= n);
    el.classList.toggle("is-locked", !jobId && stage > 1);
  });
}

function setSideTab(tab, { bumpStage = true } = {}) {
  const isLayers = tab === "layers";
  tabLayers.classList.toggle("active", isLayers);
  tabMachine.classList.toggle("active", !isLayers);
  tabLayers.setAttribute("aria-selected", String(isLayers));
  tabMachine.setAttribute("aria-selected", String(!isLayers));
  panelLayers.classList.toggle("active", isLayers);
  panelMachine.classList.toggle("active", !isLayers);
  panelLayers.hidden = !isLayers;
  panelMachine.hidden = isLayers;

  if (!bumpStage || !jobId) return;
  if (isLayers) {
    setStage(Math.max(currentStage, 1) === 1 ? 1 : Math.min(currentStage, 1) || 1);
    // Stay on preview unless already further along — don't regress past convert
    if (currentStage < 2) setStage(1);
  } else if (currentStage < 3) {
    setStage(2);
  }
}

function renderFiles(files) {
  fileListEl.innerHTML = "";
  for (const f of files) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${f.name}</span><span class="kind">${f.kind}</span>`;
    fileListEl.append(li);
  }
}

async function loadPreview(id) {
  setStatus("Building colored layer preview…");
  const res = await fetch(`/api/jobs/${id}/preview`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Preview failed");
  await board.setPreview(data);
  renderLayerToggles(
    layerTogglesEl,
    data.layers,
    (name, visible) => board.setVisible(name, visible),
    (names, visible) => board.setGroupVisible(names, visible),
    data.drills || []
  );
  renderBomTable(bomPanelEl, data.bom || [], data.bom_source);
  if (data.warnings && data.warnings.length) {
    setStatus(data.warnings.join(" · "), "error");
  } else {
    const bomN = (data.bom || []).length;
    setStatus(
      `Stage 1 Preview ready · ${data.layers.length} layers · ${data.drills.length} drills` +
        (bomN ? ` · ${bomN} BOM parts` : ""),
      "ok"
    );
  }
  setStage(1);
  setSideTab("layers", { bumpStage: false });
  setGenerateEnabled(true);
}

async function onZip(file) {
  try {
    setStatus(`Uploading ${file.name}…`);
    setGenerateEnabled(false);
    downloadsEl.innerHTML = "";
    showToolpathPreview(toolpathImg, null);
    renderBomTable(bomPanelEl, [], null);
    const uploaded = await uploadZip(file);
    jobId = uploaded.job_id;
    renderFiles(uploaded.files);
    setStatus(uploaded.message, "ok");
    await loadPreview(jobId);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    setGenerateEnabled(false);
  }
}

async function runGenerate() {
  if (!jobId) return;
  try {
    setSideTab("machine", { bumpStage: false });
    setStage(3);
    setStatus("Stage 3 · Generating CNC G-code…");
    setGenerateEnabled(false);
    const settings = readSettings(settingsForm);
    const result = await generateJob(jobId, settings);
    renderDownloads(downloadsEl, jobId, result.files);
    showToolpathPreview(toolpathImg, result.toolpath_preview_png_base64);
    setStatus(`Stage 4 Convert ready · ${result.message}`, "ok");
    setStage(4);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    setStage(2);
  } finally {
    setGenerateEnabled(!!jobId);
  }
}

setupDropzone({
  dropzone: document.getElementById("dropzone"),
  input: document.getElementById("file-input"),
  onFile: onZip,
  setStatus,
});

tabLayers.addEventListener("click", () => setSideTab("layers"));
tabMachine.addEventListener("click", () => {
  if (!jobId) {
    setStatus("Upload a Gerber zip first (Stage 1 Preview)", "error");
    return;
  }
  setSideTab("machine");
});

pills[1].addEventListener("click", () => {
  if (!jobId) return;
  setSideTab("layers");
  setStage(Math.max(1, Math.min(currentStage, 1)) || 1);
  if (currentStage > 1) setStage(currentStage); // keep progress highlight via active<=n
  setStage(Math.max(currentStage, 1));
  setSideTab("layers", { bumpStage: false });
  if (currentStage < 2) setStage(1);
});

pills[2].addEventListener("click", () => {
  if (!jobId) {
    setStatus("Upload a Gerber zip first (Stage 1 Preview)", "error");
    return;
  }
  setSideTab("machine");
});

pills[3].addEventListener("click", () => {
  if (!jobId) return;
  setSideTab("machine", { bumpStage: false });
  runGenerate();
});

pills[4].addEventListener("click", () => {
  if (!jobId) return;
  setSideTab("machine", { bumpStage: false });
  if (currentStage >= 4) setStage(4);
  else setStatus("Generate G-code first (Stage 3)", "error");
});

document.getElementById("btn-fit").addEventListener("click", () => board.fit());
document.getElementById("btn-zoom-in").addEventListener("click", () => board.zoom(1.2));
document.getElementById("btn-zoom-out").addEventListener("click", () => board.zoom(1 / 1.2));

btnGenerate.addEventListener("click", () => {
  setSideTab("machine", { bumpStage: false });
  setStage(2);
  runGenerate();
});
btnGenerateMachine.addEventListener("click", runGenerate);
