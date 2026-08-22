/** Machine tools table from PAEN_TOOLS.tlslibrary. */

let selectedToolIndex = -1;
let toolsCache = { columns: [], tools: [] };

const LIST_COLUMNS = [
  { keys: ["Number", "number", "tool"], label: "Number" },
  { keys: ["Name", "name"], label: "Name", className: "col-name" },
  { keys: ["Type", "type"], label: "Type" },
  { keys: ["Diameter(D)", "Diameter", "diameter_mm", "diameter"], label: "Diameter" },
  { keys: ["Tip Diameter(F)", "Tip Diameter", "tip"], label: "Tip Diameter" },
];

const DETAIL_GROUPS = [
  {
    heading: "Geometry",
    fields: [
      { keys: ["Angle(A)", "Angle"], label: "Angle" },
      { keys: ["Half Angle(A)", "Half Angle"], label: "Half Angle" },
      { keys: ["CornerRadius(R)", "CornerRadius"], label: "Corner Radius" },
      { keys: ["Specification"], label: "Specification" },
      { keys: ["Screw pitch"], label: "Screw Pitch" },
      { keys: ["Hole Diameter"], label: "Hole Diameter" },
    ],
  },
  {
    heading: "PCB",
    fields: [
      { keys: ["Material"], label: "Material" },
      { keys: ["Spindle Speed", "spindle_rpm"], label: "Spindle Speed" },
      { keys: ["Step Over", "step_over"], label: "Step Over" },
      { keys: ["Step Down", "step_down"], label: "Step Down" },
      { keys: ["Feed Rate", "feed_mm_min"], label: "Feed Rate" },
      { keys: ["Plunge Rate", "plunge_mm_min"], label: "Plunge Rate" },
      { keys: ["Coolant"], label: "Coolant" },
    ],
  },
];

export function getSelectedTool() {
  if (selectedToolIndex < 0 || selectedToolIndex >= toolsCache.tools.length) {
    return null;
  }
  return toolsCache.tools[selectedToolIndex];
}

export function getSelectedToolNumber(fallback = 2) {
  const n = toolNumber(getSelectedTool());
  return Number.isFinite(n) ? n : fallback;
}

export function getSelectedToolCuts() {
  const tool = getSelectedTool();
  const n = (keys, fallback) => {
    const raw = toolValue(tool, keys);
    const num = Number(String(raw ?? "").replace(/[^\d.+-]/g, ""));
    return Number.isFinite(num) ? num : fallback;
  };
  const coolantRaw = toolValue(tool, ["Coolant"]);
  let coolant = true;
  if (coolantRaw != null && String(coolantRaw).trim() !== "") {
    coolant = ["y", "yes", "true", "1", "on"].includes(String(coolantRaw).trim().toLowerCase());
  }
  return {
    feed_mm_min: n(["Feed Rate", "feed_mm_min"], 2000),
    plunge_mm_min: n(["Plunge Rate", "plunge_mm_min"], 200),
    spindle_rpm: n(["Spindle Speed", "spindle_rpm"], 12000),
    step_over_percent: n(["Step Over"], 50),
    step_down_mm: n(["Step Down"], 0.1),
    coolant,
  };
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

function toolNumber(tool) {
  if (!tool) return NaN;
  for (const key of Object.keys(tool)) {
    const lk = key.toLowerCase();
    if (lk === "tool" || lk === "number" || lk === "tool_number" || lk === "t" || lk === "#") {
      const n = Number(tool[key]);
      if (Number.isFinite(n)) return n;
    }
  }
  return NaN;
}

function toolValue(tool, keys) {
  if (!tool) return null;
  const lower = {};
  for (const [k, v] of Object.entries(tool)) {
    lower[k.toLowerCase()] = v;
  }
  for (const key of keys) {
    if (tool[key] != null && String(tool[key]).trim() !== "") return tool[key];
    const v = lower[key.toLowerCase()];
    if (v != null && String(v).trim() !== "") return v;
  }
  return null;
}

function renderToolsTable(data, { metaEl, emptyEl, tableEl }) {
  toolsCache = {
    columns: data.columns || [],
    tools: data.tools || [],
  };
  selectedToolIndex = toolsCache.tools.findIndex((tool) => toolNumber(tool) === 2);
  if (selectedToolIndex < 0) selectedToolIndex = toolsCache.tools.length ? 0 : -1;

  if (metaEl) {
    metaEl.textContent = data.message || (data.source ? `Source: ${data.source}` : "No tool library loaded");
  }

  if (!toolsCache.tools.length) {
    tableEl.hidden = true;
    emptyEl.hidden = false;
    emptyEl.textContent =
      data.message ||
      "PAEN_TOOLS.tlslibrary not found. Place it in samples/ or use Upload library.";
    renderToolProperties(null);
    return;
  }

  emptyEl.hidden = true;
  tableEl.hidden = false;
  fillListTable(tableEl, toolsCache.tools);
  selectTool(selectedToolIndex);
}

function fillListTable(tableEl, tools) {
  const thead = tableEl.querySelector("thead");
  const tbody = tableEl.querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  const headRow = document.createElement("tr");
  for (const col of LIST_COLUMNS) {
    const th = document.createElement("th");
    th.textContent = col.label;
    headRow.append(th);
  }
  thead.append(headRow);

  tools.forEach((tool, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.toolIndex = String(idx);
    for (const col of LIST_COLUMNS) {
      const td = document.createElement("td");
      if (col.className) td.className = col.className;
      const val = toolValue(tool, col.keys);
      td.textContent = val == null || val === "" ? "—" : String(val);
      tr.append(td);
    }
    tr.addEventListener("click", () => selectTool(idx));
    tbody.append(tr);
  });
}

function selectTool(idx) {
  selectedToolIndex = idx;
  document.querySelectorAll("#tools-table tbody tr").forEach((row) => {
    row.classList.toggle("is-selected", Number(row.dataset.toolIndex) === selectedToolIndex);
  });
  const tool = getSelectedTool();
  renderToolProperties(tool);
}

function renderToolProperties(tool) {
  const wrap = document.getElementById("tool-props");
  const meta = document.getElementById("tool-props-meta");
  const grid = document.getElementById("tool-props-grid");
  if (!wrap || !grid) return;

  if (!tool) {
    wrap.hidden = true;
    grid.innerHTML = "";
    if (meta) meta.textContent = "";
    return;
  }

  wrap.hidden = false;
  const number = toolNumber(tool);
  const name = toolValue(tool, ["Name", "name"]);
  if (meta) {
    meta.textContent = Number.isFinite(number)
      ? `T${number}${name ? ` · ${name}` : ""} · PCB`
      : "PCB cutting values";
  }

  grid.innerHTML = "";
  for (const group of DETAIL_GROUPS) {
    const heading = document.createElement("div");
    heading.className = "prop-heading";
    heading.textContent = group.heading;
    grid.append(heading);
    for (const field of group.fields) {
      const val = toolValue(tool, field.keys);
      const item = document.createElement("div");
      item.className = "prop-item";
      const label = document.createElement("div");
      label.className = "prop-label";
      label.textContent = field.label;
      const value = document.createElement("div");
      value.className = "prop-value";
      value.textContent = val == null || val === "" ? "—" : String(val);
      item.append(label, value);
      grid.append(item);
    }
  }
}
