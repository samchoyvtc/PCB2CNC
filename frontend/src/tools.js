/** Machine tools table from PAEN_TOOLS.tlslibrary. */

let selectedToolIndex = -1;
let toolsCache = { columns: [], tools: [] };

export function getSelectedTool() {
  if (selectedToolIndex < 0 || selectedToolIndex >= toolsCache.tools.length) {
    return null;
  }
  return toolsCache.tools[selectedToolIndex];
}

export function getSelectedToolNumber(fallback = 2) {
  const tool = getSelectedTool();
  if (!tool) return fallback;
  for (const key of Object.keys(tool)) {
    const lk = key.toLowerCase();
    if (lk === "tool" || lk === "number" || lk === "tool_number" || lk === "t" || lk === "#") {
      const n = Number(tool[key]);
      if (Number.isFinite(n)) return n;
    }
  }
  return fallback;
}

export async function loadMachineTools({ metaEl, emptyEl, tableEl, setStatus }) {
  const res = await fetch("/api/machine/tools");
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Failed to load tool library");
  renderToolsTable(data, { metaEl, emptyEl, tableEl });
  if (setStatus && data.message) setStatus(data.message, data.tools?.length ? "ok" : "");
  return data;
}

export async function uploadMachineTools(file, { metaEl, emptyEl, tableEl, setStatus }) {
  const body = new FormData();
  body.append("file", file);
  const res = await fetch("/api/machine/tools/upload", { method: "POST", body });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    throw new Error(text || "Upload failed");
  }
  if (!res.ok) throw new Error(data.detail || text || "Upload failed");
  renderToolsTable(data, { metaEl, emptyEl, tableEl });
  if (setStatus) setStatus(data.message || `Loaded tools from ${file.name}`, "ok");
  return data;
}

function renderToolsTable(data, { metaEl, emptyEl, tableEl }) {
  toolsCache = {
    columns: data.columns || [],
    tools: data.tools || [],
  };
  selectedToolIndex = toolsCache.tools.length ? 0 : -1;

  if (metaEl) {
    metaEl.textContent = data.message || (data.source ? `Source: ${data.source}` : "No tool library loaded");
  }

  const thead = tableEl.querySelector("thead");
  const tbody = tableEl.querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  if (!toolsCache.tools.length) {
    tableEl.hidden = true;
    emptyEl.hidden = false;
    emptyEl.textContent =
      data.message ||
      "PAEN_TOOLS.tlslibrary not found. Place it in samples/ or use Upload library.";
    return;
  }

  emptyEl.hidden = true;
  tableEl.hidden = false;

  const headRow = document.createElement("tr");
  for (const col of toolsCache.columns) {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.append(th);
  }
  thead.append(headRow);

  toolsCache.tools.forEach((tool, idx) => {
    const tr = document.createElement("tr");
    if (idx === selectedToolIndex) tr.classList.add("is-selected");
    for (const col of toolsCache.columns) {
      const td = document.createElement("td");
      const val = tool[col];
      td.textContent = val == null || val === "" ? "—" : String(val);
      tr.append(td);
    }
    tr.addEventListener("click", () => {
      selectedToolIndex = idx;
      tbody.querySelectorAll("tr").forEach((row, i) => {
        row.classList.toggle("is-selected", i === idx);
      });
      applyToolToSettings(tool);
    });
    tbody.append(tr);
  });

  if (selectedToolIndex >= 0) {
    applyToolToSettings(toolsCache.tools[selectedToolIndex]);
  }
}

function applyToolToSettings(tool) {
  if (!tool) return;
  const form = document.getElementById("settings-form");
  if (!form) return;

  const map = [
    [["feed", "feed_mm_min", "feedrate", "xyfeed"], "feed_mm_min"],
    [["plunge", "plunge_mm_min", "zfeed"], "plunge_mm_min"],
    [["spindle", "rpm", "spindle_rpm", "speed"], "spindle_rpm"],
    [["depth", "depth_mm", "engraving_depth_mm", "engrave"], "engraving_depth_mm"],
    [["safe_z", "safe_z_mm"], "safe_z_mm"],
    [["stock", "stock_thickness_mm", "thickness"], "stock_thickness_mm"],
  ];

  const lower = {};
  for (const [k, v] of Object.entries(tool)) {
    lower[k.toLowerCase().replace(/\s+/g, "_")] = v;
  }

  for (const [aliases, field] of map) {
    const el = form.elements[field];
    if (!el) continue;
    for (const alias of aliases) {
      if (lower[alias] != null && String(lower[alias]).trim() !== "") {
        const n = Number(String(lower[alias]).replace(/[^\d.+-]/g, ""));
        if (Number.isFinite(n)) {
          el.value = String(n);
          break;
        }
      }
    }
  }
}
