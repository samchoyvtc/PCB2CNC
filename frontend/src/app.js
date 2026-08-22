import { setupDropzone, uploadZip } from "./upload.js";
import { BoardPreview, renderLayerToggles } from "./preview.js";
import { readSettings } from "./settings.js";
import { generateJob, renderDownloads, showToolpathPreview } from "./output.js";
import {
  getSelectedToolNumber,
  listMachineTools,
  loadMachineTools,
  uploadMachineTools,
} from "./tools.js";
import { mountGenerateForm, readGeneratePlan } from "./generate.js";

const statusEl = document.getElementById("status");
const fileListEl = document.getElementById("file-list");
const layerTogglesEl = document.getElementById("layer-toggles");
const downloadsEl = document.getElementById("downloads");
const toolpathImg = document.getElementById("toolpath-preview");
const btnNext = document.getElementById("btn-next");
const btnReset = document.getElementById("btn-reset");
const btnResetHeader = document.getElementById("btn-reset-header");
const fileInput = document.getElementById("file-input");
const settingsForm = document.getElementById("settings-form");
const panelInput = document.getElementById("panel-input");
const panelPreview = document.getElementById("panel-preview");
const panelLayers = document.getElementById("panel-layers");
const panelMachineWrap = document.getElementById("panel-machine-wrap");
const panelMachine = document.getElementById("panel-machine");
const panelGenerateWrap = document.getElementById("panel-generate-wrap");
const convertSection = document.getElementById("convert-section");
const progressWrap = document.getElementById("progress-wrap");
const progressFill = document.getElementById("progress-fill");
const progressLabel = document.getElementById("progress-label");
const progressMeta = document.getElementById("progress-meta");
const progressBar = document.getElementById("progress-bar");
const toolsMeta = document.getElementById("tools-meta");
const toolsEmpty = document.getElementById("tools-empty");
const toolsTable = document.getElementById("tools-table");
const toolsFileInput = document.getElementById("tools-file-input");

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
let toolsLoaded = false;
let lastPreview = null;
let generateMountedFor = null;
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
  btnResetHeader.disabled = !enabled;
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
  lastPreview = null;
  generateMountedFor = null;
  fileInput.value = "";
  fileListEl.innerHTML = "";
  layerTogglesEl.innerHTML = "";
  downloadsEl.innerHTML = "";
  showToolpathPreview(toolpathImg, null);
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
  if (convertSection) {
    convertSection.hidden = n < 4;
  }
  updateNextButton();
}

function previewTitle(kind) {
  if (kind === "copper") return "CNC path · Copper engraving";
  if (kind === "drill") return "CNC path · Drilling";
  if (kind === "outline") return "CNC path · Board outline";
  return "Board preview";
}

function setPreviewHeading(text) {
  const heading = panelPreview?.querySelector("h2");
  if (heading) heading.textContent = text;
}

function showPanel(tab) {
  const isMachine = tab === "machine";
  const isGenerate = tab === "generate";
  document.body.classList.toggle("view-machine", isMachine);
  document.body.classList.toggle("view-generate", isGenerate);
  document.body.classList.toggle("view-preview", !isMachine);

  panelInput.hidden = isMachine || isGenerate;
  panelPreview.hidden = isMachine;
  panelLayers.hidden = isMachine || isGenerate;
  panelMachineWrap.hidden = !isMachine;
  panelMachine.hidden = !isMachine;
  if (panelGenerateWrap) panelGenerateWrap.hidden = !isGenerate;

  if (!isGenerate) {
    board.setToolpaths([]);
    if (panelGenerateWrap) panelGenerateWrap._overlays = {};
    setPreviewHeading("Board preview");
  }

  if (isMachine) {
    ensureToolsLoaded();
  }
  if (isGenerate) {
    refreshGenerateForm();
  }
  if (!isMachine) {
    requestAnimationFrame(() => {
      board.resize();
      if (isGenerate || tab === "layers") board.fit();
    });
  }
}

async function ensureToolsLoaded() {
  if (toolsLoaded) return;
  try {
    await loadMachineTools({
      metaEl: toolsMeta,
      emptyEl: toolsEmpty,
      tableEl: toolsTable,
      setStatus,
    });
    toolsLoaded = true;
  } catch (err) {
    console.error(err);
    toolsMeta.textContent = err.message || String(err);
    toolsEmpty.hidden = false;
    toolsEmpty.textContent = err.message || String(err);
    toolsTable.hidden = true;
  }
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

function goGenerate() {
  if (!jobId) {
    setStatus("Upload a Gerber zip first (Stage 1 Preview)", "error");
    return;
  }
  if (!lastPreview) {
    setStatus("Finish Stage 1 Preview before generating", "error");
    return;
  }
  showPanel("generate");
  if (currentStage < 4) setStage(3);
}

function generatePreviewCtx() {
  return {
    getJobId: () => jobId,
    getSettings: () => {
      const settings = readSettings(settingsForm);
      settings.tool_number = getSelectedToolNumber(settings.tool_number || 2);
      return settings;
    },
    setStatus,
    onPathPreview: (result, op, visible = true) => {
      if (!panelGenerateWrap._overlays) panelGenerateWrap._overlays = {};
      if (visible) panelGenerateWrap._overlays[op] = result.paths || [];
      else delete panelGenerateWrap._overlays[op];
      const keys = Object.keys(panelGenerateWrap._overlays);
      board.setToolpaths(keys.flatMap((key) => panelGenerateWrap._overlays[key] || []));
      if (!keys.length) setPreviewHeading("Board preview");
      else if (keys.length === 1) setPreviewHeading(previewTitle(keys[0].startsWith("drill") ? "drill" : keys[0]));
      else setPreviewHeading(`CNC path · ${keys.map((key) => (key.startsWith("drill") ? "drill" : key)).join(" + ")}`);
      requestAnimationFrame(() => {
        board.resize();
        board.draw();
      });
    },
  };
}

async function refreshGenerateForm() {
  await ensureToolsLoaded();
  if (!lastPreview || !panelGenerateWrap) return;
  if (generateMountedFor === jobId
      && panelGenerateWrap.querySelector(".gen-drill-depth")
      && panelGenerateWrap.querySelector(".gen-size-strategy")
      && panelGenerateWrap.querySelector("#gen-tab-offset")) {
    panelGenerateWrap._previewCtx = generatePreviewCtx();
    return;
  }
  mountGenerateForm(panelGenerateWrap, {
    preview: lastPreview,
    tools: listMachineTools(),
    ...generatePreviewCtx(),
  });
  generateMountedFor = jobId;
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
  const startText = await startRes.text();
  let startData = {};
  try {
    startData = startText ? JSON.parse(startText) : {};
  } catch (_) {
    throw new Error(startText || "Failed to start preview");
  }
  if (!startRes.ok) {
    throw new Error(startData.detail || startText || "Failed to start preview");
  }

  let data = null;
  for (;;) {
    const res = await fetch(`/api/jobs/${id}/preview/progress`);
    const text = await res.text();
    let prog = {};
    try {
      prog = text ? JSON.parse(text) : {};
    } catch (_) {
      await sleep(200);
      continue;
    }
    if (!res.ok) throw new Error(prog.detail || text || "Preview progress failed");

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
  lastPreview = data;
  generateMountedFor = null;
  await board.setPreview(data);
  renderLayerToggles(
    layerTogglesEl,
    data.layers,
    (name, visible) => board.setVisible(name, visible),
    (names, visible) => board.setGroupVisible(names, visible),
    data.drills || []
  );
  if (data.warnings && data.warnings.length) {
    setStatus(data.warnings.join(" · "), "error");
  } else {
    setStatus(
      `Stage 1 Preview ready · ${data.layers.length} layers · ${data.drills.length} drills`,
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
    await refreshGenerateForm();
    showPanel("generate");
    setStage(3);
    setStatus("Stage 3 · Generating CNC G-code…");
    busy = true;
    updateNextButton();
    const settings = readSettings(settingsForm);
    settings.tool_number = getSelectedToolNumber(settings.tool_number || 2);
    const plan = readGeneratePlan(panelGenerateWrap);
    const result = await generateJob(jobId, settings, plan);
    renderDownloads(downloadsEl, jobId, result.files);
    showToolpathPreview(toolpathImg, result.toolpath_preview_png_base64);
    setStatus(`Stage 4 Convert ready · ${result.message}`, "ok");
    setStage(4);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    setStage(3);
    showPanel("generate");
  } finally {
    busy = false;
    updateNextButton();
  }
}

async function nextStep() {
  if (!jobId || busy) return;
  if (currentStage <= 1) {
    goMachine();
    setStatus("Stage 2 · Board setting, then press Next step", "ok");
    return;
  }
  if (currentStage === 2) {
    goGenerate();
    setStatus("Stage 3 · Choose layers and tools, then press Next step to generate", "ok");
    return;
  }
  if (currentStage === 3) {
    await runGenerate();
  }
}

function doReset() {
  resetJob();
  fileInput.click();
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
  goGenerate();
});
pills[4].addEventListener("click", () => {
  if (!jobId) return;
  if (currentStage >= 4) {
    showPanel("generate");
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
btnReset.addEventListener("click", doReset);
btnResetHeader.addEventListener("click", doReset);

document.getElementById("btn-reload-tools").addEventListener("click", async () => {
  toolsLoaded = false;
  generateMountedFor = null;
  await ensureToolsLoaded();
});

toolsFileInput.addEventListener("change", async () => {
  const file = toolsFileInput.files && toolsFileInput.files[0];
  toolsFileInput.value = "";
  if (!file) return;
  try {
    await uploadMachineTools(file, {
      metaEl: toolsMeta,
      emptyEl: toolsEmpty,
      tableEl: toolsTable,
      setStatus,
    });
    toolsLoaded = true;
    generateMountedFor = null;
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
  }
});

showPanel("layers");
updateNextButton();
