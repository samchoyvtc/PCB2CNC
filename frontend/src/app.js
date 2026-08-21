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
const btnNext = document.getElementById("btn-next");
const btnReset = document.getElementById("btn-reset");
const fileInput = document.getElementById("file-input");
const settingsForm = document.getElementById("settings-form");
const panelLayers = document.getElementById("panel-layers");
const panelMachine = document.getElementById("panel-machine");
const progressWrap = document.getElementById("progress-wrap");
const progressFill = document.getElementById("progress-fill");
const progressLabel = document.getElementById("progress-label");
const progressMeta = document.getElementById("progress-meta");
const progressBar = document.getElementById("progress-bar");

const pills = {
  1: document.getElementById("pill-1"),
  2: document.getElementById("pill-2"),
  3: document.getElementById("pill-3"),
  4: document.getElementById("pill-4"),
};

const NEXT_LABELS = {
  1: "Next step · Machine",
  2: "Next step · Generate",
  3: "Next step · Convert",
  4: "Done · Convert ready",
};

let jobId = null;
let currentStage = 1;
let busy = false;
const board = new BoardPreview(document.getElementById("board-canvas"));

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function updateNextButton() {
  btnNext.textContent = NEXT_LABELS[currentStage] || "Next step";
  btnNext.disabled = !jobId || busy || currentStage >= 4;
}

function setResetEnabled(enabled) {
  btnReset.disabled = !enabled;
}

function setProgress(visible, percent = 0, label = "", meta = "") {
  progressWrap.hidden = !visible;
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  progressFill.style.width = `${pct}%`;
  progressBar.setAttribute("aria-valuenow", String(pct));
  progressLabel.textContent = label || "Building colored layer preview…";
  progressMeta.textContent = meta || `${pct}%`;
}

function resetJob() {
  jobId = null;
  currentStage = 1;
  busy = false;
  fileInput.value = "";
  fileListEl.innerHTML = "";
  layerTogglesEl.innerHTML = "";
  downloadsEl.innerHTML = "";
  showToolpathPreview(toolpathImg, null);
  renderBomTable(bomPanelEl, [], null);
  board.clear();
  setResetEnabled(false);
  setProgress(false);
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
  updateNextButton();
}

function showPanel(tab) {
  const isLayers = tab === "layers";
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadPreview(id) {
  setStatus("Building colored layer preview…");
  setProgress(true, 0, "Starting preview…", "0%");
  busy = true;
  updateNextButton();

  const startRes = await fetch(`/api/jobs/${id}/preview/start`, { method: "POST" });
  const startData = await startRes.json().catch(() => ({}));
  if (!startRes.ok) throw new Error(startData.detail || "Failed to start preview");

  let data = null;
  for (;;) {
    const res = await fetch(`/api/jobs/${id}/preview/progress`);
    const prog = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(prog.detail || "Preview progress failed");

    setProgress(
      true,
      prog.percent || 0,
      prog.message || "Building colored layer preview…",
      `${prog.percent || 0}%` +
        (prog.total ? ` · ${prog.current || 0}/${prog.total}` : "")
    );

    if (prog.state === "done" && prog.result) {
      data = prog.result;
      break;
    }
    if (prog.state === "error") {
      throw new Error(prog.error || "Preview failed");
    }
    await sleep(200);
  }

  setProgress(true, 100, "Preview complete", "100%");
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
  setTimeout(() => setProgress(false), 600);
  setStage(1);
  showPanel("layers");
  busy = false;
  setResetEnabled(true);
  updateNextButton();
}

async function onZip(file) {
  try {
    setStatus(`Uploading ${file.name}…`);
    busy = true;
    updateNextButton();
    downloadsEl.innerHTML = "";
    showToolpathPreview(toolpathImg, null);
    renderBomTable(bomPanelEl, [], null);
    setProgress(true, 5, "Uploading zip…", "5%");
    const uploaded = await uploadZip(file);
    jobId = uploaded.job_id;
    renderFiles(uploaded.files);
    setStatus(uploaded.message, "ok");
    setResetEnabled(true);
    await loadPreview(jobId);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    busy = false;
    updateNextButton();
    setProgress(false);
  }
}

async function runGenerate() {
  if (!jobId) return;
  try {
    showPanel("machine");
    setStage(3);
    setStatus("Stage 3 · Generating CNC G-code…");
    busy = true;
    updateNextButton();
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
    busy = false;
    updateNextButton();
  }
}

async function nextStep() {
  if (!jobId || busy) return;
  if (currentStage <= 1) {
    goMachine();
    setStatus("Stage 2 · Configure machine settings, then press Next step", "ok");
    return;
  }
  if (currentStage === 2) {
    await runGenerate();
    return;
  }
  if (currentStage === 3) {
    showPanel("machine");
    setStage(4);
    setStatus("Stage 4 · Download converted .nc files below", "ok");
  }
}

setupDropzone({
  dropzone: document.getElementById("dropzone"),
  input: document.getElementById("file-input"),
  onFile: onZip,
  setStatus,
});

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

btnNext.addEventListener("click", nextStep);
btnReset.addEventListener("click", () => {
  resetJob();
  fileInput.click();
});

updateNextButton();
