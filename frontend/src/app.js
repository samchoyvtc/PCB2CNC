import { setupDropzone, uploadZip } from "./upload.js";
import { BoardPreview, renderLayerToggles } from "./preview.js";
import { readSettings } from "./settings.js";
import { generateJob, renderDownloads, showToolpathPreview } from "./output.js";

const statusEl = document.getElementById("status");
const fileListEl = document.getElementById("file-list");
const layerTogglesEl = document.getElementById("layer-toggles");
const downloadsEl = document.getElementById("downloads");
const toolpathImg = document.getElementById("toolpath-preview");
const btnGenerate = document.getElementById("btn-generate");
const settingsForm = document.getElementById("settings-form");
const tabLayers = document.getElementById("tab-layers");
const tabMachine = document.getElementById("tab-machine");
const panelLayers = document.getElementById("panel-layers");
const panelMachine = document.getElementById("panel-machine");

const pills = {
  1: document.getElementById("pill-1"),
  2: document.getElementById("pill-2"),
  3: document.getElementById("pill-3"),
};

let jobId = null;
const board = new BoardPreview(document.getElementById("board-canvas"));

function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${kind}`.trim();
}

function setStage(n) {
  Object.entries(pills).forEach(([k, el]) => {
    el.classList.toggle("active", Number(k) <= n);
  });
}

function setSideTab(tab) {
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
  if (data.warnings && data.warnings.length) {
    setStatus(data.warnings.join(" · "), "error");
  } else {
    setStatus(`Preview ready · ${data.layers.length} layers · ${data.drills.length} drills`, "ok");
  }
  setStage(1);
  setSideTab("layers");
  btnGenerate.disabled = false;
}

async function onZip(file) {
  try {
    setStatus(`Uploading ${file.name}…`);
    btnGenerate.disabled = true;
    downloadsEl.innerHTML = "";
    showToolpathPreview(toolpathImg, null);
    const uploaded = await uploadZip(file);
    jobId = uploaded.job_id;
    renderFiles(uploaded.files);
    setStatus(uploaded.message, "ok");
    await loadPreview(jobId);
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
    btnGenerate.disabled = true;
  }
}

setupDropzone({
  dropzone: document.getElementById("dropzone"),
  input: document.getElementById("file-input"),
  onFile: onZip,
  setStatus,
});

tabLayers.addEventListener("click", () => setSideTab("layers"));
tabMachine.addEventListener("click", () => setSideTab("machine"));

document.getElementById("btn-fit").addEventListener("click", () => board.fit());
document.getElementById("btn-zoom-in").addEventListener("click", () => board.zoom(1.2));
document.getElementById("btn-zoom-out").addEventListener("click", () => board.zoom(1 / 1.2));

btnGenerate.addEventListener("click", async () => {
  if (!jobId) return;
  try {
    setStage(2);
    setStatus("Generating CNC G-code…");
    btnGenerate.disabled = true;
    const settings = readSettings(settingsForm);
    const result = await generateJob(jobId, settings);
    renderDownloads(downloadsEl, jobId, result.files);
    showToolpathPreview(toolpathImg, result.toolpath_preview_png_base64);
    setStatus(result.message, "ok");
    setStage(3);
    setSideTab("machine");
  } catch (err) {
    console.error(err);
    setStatus(err.message || String(err), "error");
  } finally {
    btnGenerate.disabled = !jobId;
  }
});
