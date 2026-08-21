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
const btnReset = document.getElementById("btn-reset");
const fileInput = document.getElementById("file-input");
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

function setResetEnabled(enabled) {
  btnReset.disabled = !enabled;
}

function resetJob() {
  jobId = null;
  currentStage = 1;
  fileInput.value = "";
  fileListEl.innerHTML = "";
  layerTogglesEl.innerHTML = "";
  downloadsEl.innerHTML = "";
  showToolpathPreview(toolpathImg, null);
  renderBomTable(bomPanelEl, [], null);
  board.clear();
  setGenerateEnabled(false);
  setResetEnabled(false);
  setStage(1);
  showPanel("layers");
  setStatus("Ready for a new zip…");
}

function setStage(n) {
  currentStage = n;
  Object.entries(pills).forEach(([k, el]) => {
    const stage = Number(k);
    el.classList.toggle("active", stage <= n);
    el.classList.toggle("is-locked", !jobId && stage > 1);
  });
}

function showPanel(tab) {
  const isLayers = tab === "layers";
  tabLayers.classList.toggle("active", isLayers);
  tabMachine.classList.toggle("active", !isLayers);
  tabLayers.setAttribute("aria-selected", String(isLayers));
  tabMachine.setAttribute("aria-selected", String(!isLayers));
  panelLayers.classList.toggle("active", isLayers);
  panelMachine.classList.toggle("active", !isLayers);
  panelLayers.hidden = !isLayers;
  panelMachine.hidden = isLayers;
}

function goPreview() {
  if (!jobId) {
    setStatus("Upload a Gerber zip to start Stage 1 Preview", "error");
    return;
  }
  showPanel("layers");
  setStage(Math.max(currentStage, 1) === 1 ? 1 : currentStage < 2 ? 1 : currentStage);
  // Viewing preview: if not yet past machine, mark stage 1
  if (currentStage < 2) setStage(1);
}

function goMachine() {
  if (!jobId) {
    setStatus("Upload a Gerber zip first (Stage 1 Preview)", "error");
    return;
  }
  showPanel("machine");
  if (currentStage < 3) setStage(2);
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
  showPanel("layers");
  setGenerateEnabled(true);
  btnReset.disabled = false;
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
    showPanel("machine");
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

tabLayers.addEventListener("click", goPreview);
tabMachine.addEventListener("click", goMachine);
pills[1].addEventListener("click", goPreview);
pills[2].addEventListener("click", goMachine);
pills[3].addEventListener("click", () => {
  if (!jobId) return;
  runGenerate();
});
pills[4].addEventListener("click", () => {
  if (!jobId) return;
  showPanel("machine");
  if (currentStage >= 4) {
    setStage(4);
    setStatus("Stage 4 · Download converted .nc files below", "ok");
  } else {
    setStatus("Run Stage 3 Generate first", "error");
  }
});

document.getElementById("btn-fit").addEventListener("click", () => board.fit());
document.getElementById("btn-zoom-in").addEventListener("click", () => board.zoom(1.2));
document.getElementById("btn-zoom-out").addEventListener("click", () => board.zoom(1 / 1.2));

btnGenerate.addEventListener("click", () => {
  goMachine();
  runGenerate();
});
btnGenerateMachine.addEventListener("click", runGenerate);
btnReset.addEventListener("click", () => {
  resetJob();
  fileInput.click();
});
