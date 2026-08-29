import { setupDropzone, uploadZip } from "./upload.js";
import { BoardPreview, renderLayerToggles, syncLayerToggleChecks } from "./preview.js";
import { readSettings, restoreSettingsForm, settingsFormChanged, snapshotSettingsForm } from "./settings.js";
import { fetchNcText, generateJob, parseNcJobSequence, renderDownloads, renderJobSequence, sortNcNames } from "./output.js";
import {
  getSelectedToolNumber,
  listMachineTools,
  loadMachineTools,
  uploadMachineTools,
} from "./tools.js";
import { autoPreviewAll, fillBoardSettingTools, mountGenerateForm, readGeneratePlan } from "./generate.js";

const statusEl = document.getElementById("status");
const fileListEl = document.getElementById("file-list");
const layerTogglesEl = document.getElementById("layer-toggles");
const downloadsEl = document.getElementById("downloads");
const gcodeInspect = document.getElementById("gcode-inspect");
const gcodeInspectName = document.getElementById("gcode-inspect-name");
const gcodeInspectMeta = document.getElementById("gcode-inspect-meta");
const gcodeInspectBody = document.getElementById("gcode-inspect-body");
const gcodeSequence = document.getElementById("gcode-sequence");
const gcodeSequenceBody = document.getElementById("gcode-sequence-body");
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
const panelJobsWrap = document.getElementById("panel-jobs-wrap");
const panelConvertWrap = document.getElementById("panel-convert-wrap");
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
  1: "Next step · Convert",
  2: "Next step · Convert",
  3: "Next step · Convert",
  4: "Download",
};

const PATH_HEADING = "CNC path · copper + drill + outline";

let jobId = null;
let currentStage = 1;
let busy = false;
let toolsLoaded = false;
let lastPreview = null;
let generateMountedFor = null;
let lastConvert = null;
let inspectMode = "jobs";
let settingsSnapshot = null;
const board = new BoardPreview(document.getElementById("board-canvas"));

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function updateNextButton() {
  const onGenerate = panelGenerateWrap && !panelGenerateWrap.hidden;
  const onConvert = panelConvertWrap && !panelConvertWrap.hidden;
  if (onGenerate) {
    btnNext.textContent = "Next step · Convert";
    btnNext.disabled = !jobId || busy;
    return;
  }
  if (onConvert) {
    btnNext.textContent = "Download";
    btnNext.disabled = !jobId || busy || !lastConvert;
    return;
  }
  const onMachine = panelMachineWrap && !panelMachineWrap.hidden;
  if (onMachine) {
    btnNext.textContent = "Next step · Convert";
    btnNext.disabled = true;
    return;
  }
  btnNext.textContent = NEXT_LABELS[currentStage] || "Next step";
  btnNext.disabled = !jobId || busy || currentStage >= 4;
}

function setResetEnabled(enabled) {
  btnReset.disabled = !enabled;
  btnResetHeader.disabled = !enabled;
}

let progressHideTimer = 0;

function setProgress(visible, percent = 0, label = "", meta = "") {
  if (progressHideTimer) {
    window.clearTimeout(progressHideTimer);
    progressHideTimer = 0;
  }
  progressWrap.hidden = !visible;
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  progressFill.style.width = `${pct}%`;
  progressBar.setAttribute("aria-valuenow", String(pct));
  progressLabel.textContent = label || "Building colored layer preview…";
  progressMeta.textContent = meta || `${pct}%`;
}

function hideProgressSoon(ms = 600) {
  window.clearTimeout(progressHideTimer);
  progressHideTimer = window.setTimeout(() => {
    progressHideTimer = 0;
    setProgress(false);
  }, ms);
}

function resetJob() {
  restoreSettingsForm(settingsForm, settingsSnapshot);
  jobId = null;
  currentStage = 1;
  busy = false;
  lastPreview = null;
  generateMountedFor = null;
  lastConvert = null;
  fileInput.value = "";
  fileListEl.innerHTML = "";
  layerTogglesEl.innerHTML = "";
  downloadsEl.innerHTML = "";
  clearGcodeInspect();
  board.clear();
  setResetEnabled(false);
  setProgress(false);
  setStage(1);
  showPanel("generate");
  setPreviewHeading(PATH_HEADING);
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

function previewTitle(kind) {
  if (kind === "copper") return "CNC path · Copper engraving";
  if (kind === "drill") return "CNC path · Drilling";
  if (kind === "outline") return "CNC path · Board outline";
  return PATH_HEADING;
}

function overlayLabel(key) {
  if (key.startsWith("drill")) return "drill";
  return key;
}

function setPreviewHeading(text) {
  const heading = panelPreview?.querySelector("h2");
  if (heading) heading.textContent = text;
}

function showPanel(tab) {
  const isMachine = tab === "machine";
  const isGenerate = tab === "generate";
  const isConvert = tab === "convert";
  document.body.classList.toggle("view-machine", isMachine);
  document.body.classList.toggle("view-generate", isGenerate);
  document.body.classList.toggle("view-convert", isConvert);
  document.body.classList.toggle("view-preview", !isMachine);

  panelInput.hidden = isMachine || isConvert;
  panelPreview.hidden = isMachine;
  panelLayers.hidden = true;
  panelMachineWrap.hidden = !isMachine;
  panelMachine.hidden = !isMachine;
  if (panelGenerateWrap) panelGenerateWrap.hidden = !isGenerate;
  if (panelJobsWrap) panelJobsWrap.hidden = !isConvert;
  if (panelConvertWrap) panelConvertWrap.hidden = !isConvert;

  if (!isGenerate && !isConvert) {
    board.setToolpaths([]);
    board.setMirrorX(false);
    if (panelGenerateWrap) panelGenerateWrap._overlays = {};
    setPreviewHeading(PATH_HEADING);
  }
  if (isConvert) {
    board.setMirrorX(!!panelGenerateWrap?.querySelector("#gen-mirror")?.checked);
  }

  if (isMachine) {
    ensureToolsLoaded();
  }
  if (isGenerate) {
    refreshGenerateForm();
    paintGenerateOverlays();
  }
  if (!isMachine) {
    requestAnimationFrame(() => {
      board.resize();
      if (isGenerate || isConvert) board.fit();
    });
  }
  updateNextButton();
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
    fillBoardSettingTools(listMachineTools());
  } catch (err) {
    console.error(err);
    toolsMeta.textContent = err.message || String(err);
    toolsEmpty.hidden = false;
    toolsEmpty.textContent = err.message || String(err);
    toolsTable.hidden = true;
  }
}

function goPreview() {
  if (panelMachineWrap && !panelMachineWrap.hidden) {
    restoreSettingsForm(settingsForm, settingsSnapshot);
  }
  showPanel("generate");
  if (jobId && lastPreview) setStage(3);
}

async function goMachine() {
  if (!jobId) {
    setStatus("Upload a Gerber zip first", "error");
    return;
  }
  await ensureToolsLoaded();
  settingsSnapshot = snapshotSettingsForm(settingsForm);
  showPanel("machine");
  setStatus("Change Board setting, then Apply or Cancel.");
}

function applyBoardSettings() {
  const next = snapshotSettingsForm(settingsForm);
  const changed = settingsFormChanged(settingsSnapshot, next);
  settingsSnapshot = next;
  showPanel("generate");
  if (jobId && lastPreview) setStage(3);
  if (changed && lastPreview && panelGenerateWrap) {
    void autoPreviewAll(panelGenerateWrap);
  }
  setStatus(changed ? "Board setting applied." : "Board setting unchanged.", "ok");
}

function cancelBoardSettings() {
  restoreSettingsForm(settingsForm, settingsSnapshot);
  showPanel("generate");
  if (jobId && lastPreview) setStage(3);
  setStatus("Board setting discarded.");
}

function goGenerate() {
  if (!jobId) {
    setStatus("Upload a Gerber zip first", "error");
    return;
  }
  if (!lastPreview) {
    setStatus("Wait for the board preview to finish", "error");
    return;
  }
  showPanel("generate");
  setStage(3);
}

function showSelectedGerber(name) {
  if (!name) return;
  board.showSelectedLayer(name);
  syncLayerToggleChecks(layerTogglesEl, board.visibility);
}

function paintGenerateOverlays() {
  const overlays = panelGenerateWrap?._overlays || {};
  const keys = Object.keys(overlays);
  board.setToolpaths(keys.flatMap((key) => overlays[key] || []));
  if (!keys.length) setPreviewHeading(PATH_HEADING);
  else if (keys.length === 1) setPreviewHeading(previewTitle(keys[0].startsWith("drill") ? "drill" : keys[0]));
  else setPreviewHeading(`CNC path · ${keys.map(overlayLabel).join(" + ")}`);
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
    setProgress,
    hideProgressSoon,
    onSelectLayer: showSelectedGerber,
    onMirrorChange: (on) => board.setMirrorX(on),
    onPathPreview: (result, op, visible = true) => {
      if (!panelGenerateWrap._overlays) panelGenerateWrap._overlays = {};
      if (visible) panelGenerateWrap._overlays[op] = result.paths || [];
      else delete panelGenerateWrap._overlays[op];
      paintGenerateOverlays();
      requestAnimationFrame(() => {
        board.resize();
        board.draw();
      });
    },
  };
}

async function refreshGenerateForm() {
  if (!lastPreview || !panelGenerateWrap) return;
  await ensureToolsLoaded();
  const ctx = generatePreviewCtx();
  if (generateMountedFor === jobId
      && panelGenerateWrap.querySelector(".gen-drill")
      && panelGenerateWrap.querySelector(".gen-copper-layer")) {
    panelGenerateWrap._previewCtx = ctx;
    ctx.onSelectLayer?.(panelGenerateWrap.querySelector(".gen-copper-layer")?.value);
    ctx.onMirrorChange?.(panelGenerateWrap.querySelector("#gen-mirror")?.checked);
    const overlays = panelGenerateWrap._overlays || {};
    if (!Object.keys(overlays).length) void autoPreviewAll(panelGenerateWrap);
    return;
  }
  mountGenerateForm(panelGenerateWrap, {
    preview: lastPreview,
    tools: listMachineTools(),
    ...ctx,
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
      `Board ready · ${data.layers.length} layers · ${data.drills.length} drills`,
      "ok"
    );
  }
  hideProgressSoon(600);
  setStage(3);
  showPanel("generate");
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
    clearGcodeInspect();
    lastConvert = null;
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

function planHasWork(plan) {
  return !!(plan?.copper || plan?.copper_bottom || (plan?.drills && plan.drills.length) || plan?.outline);
}

function clearGcodeInspect() {
  if (gcodeInspect) gcodeInspect.hidden = true;
  if (gcodeInspectName) gcodeInspectName.textContent = "";
  if (gcodeInspectMeta) gcodeInspectMeta.textContent = "";
  if (gcodeInspectBody) {
    gcodeInspectBody.hidden = false;
    gcodeInspectBody.textContent = "";
  }
  if (gcodeSequence) gcodeSequence.hidden = true;
  if (gcodeSequenceBody) gcodeSequenceBody.replaceChildren();
}

function overlayConvertedPaths(name) {
  const paths = lastConvert?.paths || [];
  const shown = !name || name === "all.nc" ? paths : paths.filter((path) => path.file === name);
  board.setToolpaths(shown);
  setPreviewHeading(!name || name === "all.nc" ? "CNC path · Converted" : `CNC path · ${name}`);
}

function markSelectedDownload(name) {
  for (const li of downloadsEl.querySelectorAll("li")) {
    const selected = li.dataset.name === name;
    li.classList.toggle("is-selected", selected);
    li.setAttribute("aria-selected", selected ? "true" : "false");
  }
}

let inspectSeq = 0;

function applyInspectView() {
  const wantJobs = inspectMode === "jobs";
  const hasJobs = Boolean(gcodeSequenceBody?.children.length);
  if (gcodeSequence) gcodeSequence.hidden = !wantJobs;
  if (gcodeInspectBody) gcodeInspectBody.hidden = wantJobs;
  if (!gcodeInspectMeta) return;
  if (wantJobs) {
    const n = gcodeSequenceBody?.children.length || 0;
    gcodeInspectMeta.textContent = hasJobs
      ? `${n} step${n === 1 ? "" : "s"} · mill order`
      : "No mill-order steps in this file";
    return;
  }
  const text = gcodeInspectBody?.textContent || "";
  const lines = text ? text.split(/\r?\n/).length : 0;
  gcodeInspectMeta.textContent = `${lines.toLocaleString()} lines`;
}

function showGcodeText(name, text) {
  if (gcodeInspect) gcodeInspect.hidden = false;
  if (gcodeInspectName) gcodeInspectName.textContent = name;
  const steps = parseNcJobSequence(text);
  if (gcodeSequenceBody) {
    if (steps.length) renderJobSequence(gcodeSequenceBody, steps, listMachineTools());
    else gcodeSequenceBody.replaceChildren();
  }
  if (gcodeInspectBody) {
    gcodeInspectBody.textContent = text || "";
    gcodeInspectBody.scrollTop = 0;
  }
  applyInspectView();
}

async function inspectNc(name) {
  if (!jobId || !lastConvert || !name) return;
  await ensureToolsLoaded();
  const cached = lastConvert.gcode[name];
  const sameFile = lastConvert.selected === name && cached != null && gcodeInspect && !gcodeInspect.hidden;
  lastConvert.selected = name;
  markSelectedDownload(name);
  overlayConvertedPaths(name);
  if (sameFile) return;
  if (cached != null) {
    showGcodeText(name, cached);
    return;
  }
  const seq = ++inspectSeq;
  if (gcodeInspect) gcodeInspect.hidden = false;
  if (gcodeInspectName) gcodeInspectName.textContent = name;
  if (gcodeInspectMeta) gcodeInspectMeta.textContent = "Loading…";
  if (gcodeInspectBody) gcodeInspectBody.textContent = "";
  try {
    lastConvert.gcode[name] = await fetchNcText(jobId, name);
    if (seq !== inspectSeq) return;
    showGcodeText(name, lastConvert.gcode[name] || "");
  } catch (err) {
    if (seq !== inspectSeq) return;
    if (gcodeInspectBody) gcodeInspectBody.textContent = "";
    if (gcodeInspectMeta) gcodeInspectMeta.textContent = err.message || String(err);
  }
}

function applyConvertResult(result) {
  const files = result.files || [];
  const names = sortNcNames(files);
  lastConvert = {
    files,
    paths: result.paths || [],
    selected: names.includes("all.nc") ? "all.nc" : names[0] || "",
    gcode: {},
  };
  renderDownloads(downloadsEl, jobId, lastConvert.files, {
    selected: lastConvert.selected,
    onSelect: (name) => {
      void inspectNc(name);
    },
  });
  const lede = document.getElementById("convert-lede");
  if (lede) {
    const n = lastConvert.files.length;
    lede.textContent =
      `${n} file${n === 1 ? "" : "s"} written from your Generate plan. ` +
      "Select all.nc for mill order, or a process file for its G-code.";
  }
  void inspectNc(lastConvert.selected);
}

function goConvert() {
  if (!jobId) {
    setStatus("Upload a Gerber zip first", "error");
    return;
  }
  if (!lastConvert) {
    setStatus("Press Next step · Convert to write the .nc files", "error");
    return;
  }
  showPanel("convert");
  setStage(4);
  void inspectNc(lastConvert.selected);
    setStatus("Stage 2 · Inspect or download converted .nc files", "ok");
  requestAnimationFrame(() => {
    board.resize();
    board.draw();
  });
}

async function runGenerate() {
  if (!jobId) return;
  try {
    await refreshGenerateForm();
    showPanel("generate");
    const settings = readSettings(settingsForm);
    settings.tool_number = getSelectedToolNumber(settings.tool_number || 2);
    const plan = readGeneratePlan(panelGenerateWrap);
    if (!planHasWork(plan)) {
      setStatus("Choose at least one process (copper, drill, or outline).", "error");
      setStage(3);
      return;
    }
    setStatus("Writing CNC G-code…");
    busy = true;
    updateNextButton();
    setProgress(true, 25, "Writing CNC G-code…", "25%");
    const result = await generateJob(jobId, settings, plan);
    setProgress(true, 90, "Building toolpath overlay…", "90%");
    applyConvertResult(result);
    showPanel("convert");
    setStage(4);
    setStatus(`Convert ready · ${result.message}`, "ok");
    hideProgressSoon(400);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    setStage(3);
    showPanel("generate");
    setProgress(false);
  } finally {
    busy = false;
    updateNextButton();
  }
}

async function nextStep() {
  if (!jobId || busy) return;
  if (panelGenerateWrap && !panelGenerateWrap.hidden) {
    await runGenerate();
    return;
  }
  if (panelConvertWrap && !panelConvertWrap.hidden) {
    const link =
      downloadsEl.querySelector('a[download="all.nc"]') || downloadsEl.querySelector("a");
    link?.click();
    return;
  }
  if (panelMachineWrap && !panelMachineWrap.hidden) {
    applyBoardSettings();
    return;
  }
  if (currentStage <= 2) {
    goGenerate();
    setStatus("Paths previewed. Press Next step to convert.", "ok");
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
document.getElementById("btn-open-machine")?.addEventListener("click", goMachine);
document.getElementById("btn-settings-apply")?.addEventListener("click", applyBoardSettings);
document.getElementById("btn-settings-cancel")?.addEventListener("click", cancelBoardSettings);
pills[3].addEventListener("click", () => {
  if (!jobId) return;
  goGenerate();
});
pills[4].addEventListener("click", goConvert);

document.getElementById("inspect-mode")?.addEventListener("change", (event) => {
  const value = event.target?.value;
  if (value !== "jobs" && value !== "gcode") return;
  inspectMode = value;
  applyInspectView();
});

document.getElementById("btn-fit").addEventListener("click", () => board.fit());
document.getElementById("btn-zoom-in").addEventListener("click", () => board.zoom(1.2));
document.getElementById("btn-zoom-out").addEventListener("click", () => board.zoom(1 / 1.2));
document.getElementById("hide-rapids")?.addEventListener("change", (event) => {
  board.setShowRapids(!event.target.checked);
});

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
    fillBoardSettingTools(listMachineTools());
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
  }
});

showPanel("generate");
updateNextButton();
