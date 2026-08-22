/** Stage 3 generate-plan form: copper, drills, outline + tabs. */

import { previewPath } from "./output.js";

function toolNumber(tool) {
  const n = Number(tool?.Number ?? tool?.number ?? tool?.tool);
  return Number.isFinite(n) ? n : NaN;
}

function toolTip(tool) {
  const n = Number(tool?.["Tip Diameter(F)"] ?? tool?.["Diameter(D)"] ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function toolLabel(tool) {
  const n = toolNumber(tool);
  const name = tool?.Name || tool?.name || "";
  return Number.isFinite(n) ? `T${n}${name ? ` · ${name}` : ""}` : name || "Tool";
}

function gerberLayers(preview) {
  return (preview?.layers || []).filter((layer) => layer.kind !== "drill" && layer.kind !== "bom");
}

function drillLayers(preview) {
  return (preview?.layers || []).filter((layer) => layer.kind === "drill");
}

function defaultLayer(layers, kinds, fallbackIndex = 0) {
  return layers.find((layer) => kinds.includes(layer.kind)) || layers[fallbackIndex] || null;
}

export function defaultDrillTool(diameter, tools) {
  const candidates = (tools || [])
    .map((tool) => ({ number: toolNumber(tool), tip: toolTip(tool) }))
    .filter((row) => Number.isFinite(row.number) && row.number >= 1 && row.tip > 0);
  if (!candidates.length) return 4;
  const ge = candidates.filter((row) => row.tip + 1e-6 >= diameter);
  const pool = ge.length ? ge : candidates;
  pool.sort((a, b) => Math.abs(a.tip - diameter) - Math.abs(b.tip - diameter) || a.number - b.number);
  return pool[0].number;
}

function optionHtml(value, label, selected) {
  const sel = selected ? " selected" : "";
  return `<option value="${escapeAttr(value)}"${sel}>${escapeHtml(label)}</option>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function toolSelectHtml(tools, selected) {
  return (tools || [])
    .map((tool) => {
      const n = toolNumber(tool);
      if (!Number.isFinite(n)) return "";
      return optionHtml(String(n), toolLabel(tool), n === selected);
    })
    .join("");
}

function layerSelectHtml(layers, selectedName) {
  return layers
    .map((layer) => optionHtml(layer.name, `${layer.name} · ${layer.kind}`, layer.name === selectedName))
    .join("");
}

function sizesFromLayers(layers, selectedNames) {
  const map = new Map();
  for (const layer of layers) {
    if (!selectedNames.has(layer.name)) continue;
    for (const item of layer.drill_tools || []) {
      const diameter = Number(item.diameter);
      if (!Number.isFinite(diameter) || diameter <= 0) continue;
      const key = diameter.toFixed(3);
      const prev = map.get(key) || { diameter, count: 0 };
      prev.count += Number(item.count) || 0;
      map.set(key, prev);
    }
  }
  return [...map.values()].sort((a, b) => a.diameter - b.diameter);
}

function fillSelect(selectEl, html) {
  if (!selectEl) return;
  selectEl.innerHTML = html;
}

function selectedNames(block) {
  return new Set(
    [...block.querySelectorAll('input[name="drill-layer"]:checked')].map((el) => el.value)
  );
}

function existingSizeTools(block) {
  const out = new Map();
  block.querySelectorAll("tbody tr[data-diameter]").forEach((row) => {
    const select = row.querySelector("select");
    if (select) out.set(row.dataset.diameter, Number(select.value));
  });
  return out;
}

function renderSizeRows(block, sizes, tools, previous) {
  const tbody = block.querySelector("tbody");
  const empty = block.querySelector(".gen-size-empty");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!sizes.length) {
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  for (const size of sizes) {
    const key = size.diameter.toFixed(3);
    const chosen = previous.get(key) || defaultDrillTool(size.diameter, tools);
    const tr = document.createElement("tr");
    tr.dataset.diameter = key;
    tr.innerHTML = `
      <td>${size.diameter.toFixed(3)} mm</td>
      <td>${size.count}</td>
      <td><select class="gen-size-tool">${toolSelectHtml(tools, chosen)}</select></td>
    `;
    tbody.append(tr);
  }
}

function refreshDrillSizes(block, preview, tools) {
  const sizes = sizesFromLayers(drillLayers(preview), selectedNames(block));
  renderSizeRows(block, sizes, tools, existingSizeTools(block));
}

function renderDrillBlock(preview, tools, { extra = false } = {}) {
  const drills = drillLayers(preview);
  const section = document.createElement("section");
  section.className = `gen-card gen-drill${extra ? " is-extra" : ""}`;
  const title = extra ? "Optional · PCB drilling" : "2 · PCB drilling";
  const checks = drills
    .map((layer) => {
      const checked = extra ? "" : " checked";
      const sizes = (layer.drill_tools || [])
        .map((item) => `${Number(item.diameter).toFixed(3)} mm × ${item.count}`)
        .join(", ");
      return `
        <label class="gen-check">
          <input type="checkbox" name="drill-layer" value="${escapeAttr(layer.name)}"${checked} />
          <span>
            <strong>${escapeHtml(layer.name)}</strong>
            <em>${sizes || "no hole sizes parsed"}</em>
          </span>
        </label>
      `;
    })
    .join("");

  section.innerHTML = `
    <div class="gen-card-head">
      <h3>${title}</h3>
      <div class="gen-card-actions">
        <button type="button" class="gen-preview-btn" data-op="drill">Preview</button>
        ${extra ? '<button type="button" class="gen-remove-drill">Remove</button>' : ""}
      </div>
    </div>
    <p class="gen-hint">Choose one or more drill files, then assign a tool to each hole size.</p>
    <div class="gen-checks">${checks || '<p class="gen-hint">No drill files in this zip.</p>'}</div>
    <div class="gen-size-wrap">
      <table class="gen-size-table">
        <thead>
          <tr><th>Hole Ø</th><th>Count</th><th>Tool</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <p class="gen-size-empty" hidden>Select a drill file to map hole sizes.</p>
    </div>
  `;
  return section;
}

function planForCard(root, kind, card) {
  if (kind === "copper") {
    const layer = root.querySelector("#gen-copper-layer")?.value;
    const tool = Number(root.querySelector("#gen-copper-tool")?.value);
    if (!layer) return null;
    return {
      copper: { layer, tool_number: Number.isFinite(tool) ? tool : 2 },
      drills: [],
      outline: null,
    };
  }
  if (kind === "drill") {
    const layers = [...selectedNames(card)];
    if (!layers.length) return null;
    const size_map = [...card.querySelectorAll("tbody tr[data-diameter]")].map((row) => ({
      diameter_mm: Number(row.dataset.diameter),
      tool_number: Number(row.querySelector("select")?.value) || 4,
    }));
    return { copper: null, drills: [{ layers, size_map }], outline: null };
  }
  if (kind === "outline") {
    const layer = root.querySelector("#gen-outline-layer")?.value;
    const tool = Number(root.querySelector("#gen-outline-tool")?.value);
    const tabCount = Number(root.querySelector("#gen-tab-count")?.value);
    const tabWidth = Number(root.querySelector("#gen-tab-width")?.value);
    if (!layer) return null;
    return {
      copper: null,
      drills: [],
      outline: {
        layer,
        tool_number: Number.isFinite(tool) ? tool : 4,
        tab_count: Number.isFinite(tabCount) ? tabCount : 4,
        tab_width_mm: Number.isFinite(tabWidth) ? tabWidth : 2,
      },
    };
  }
  return null;
}

function bindPreviewClicks(root) {
  if (root.dataset.previewBound) return;
  root.dataset.previewBound = "1";
  root.addEventListener("click", async (event) => {
    const btn = event.target.closest(".gen-preview-btn");
    if (!btn || !root.contains(btn)) return;
    const card = btn.closest(".gen-card");
    const ctx = root._previewCtx || {};
    const jobId = ctx.getJobId?.();
    const settings = ctx.getSettings?.();
    const plan = planForCard(root, btn.dataset.op, card);
    root.querySelectorAll(".gen-card").forEach((el) => el.classList.remove("is-previewing"));
    if (!jobId) {
      ctx.setStatus?.("Upload a Gerber zip first.", "error");
      return;
    }
    if (!plan) {
      ctx.setStatus?.(
        btn.dataset.op === "drill"
          ? "Select at least one drill file to preview."
          : "Choose a layer to preview.",
        "error"
      );
      return;
    }
    btn.disabled = true;
    ctx.setStatus?.("Building CNC path preview…");
    try {
      const result = await previewPath(jobId, settings, plan);
      card?.classList.add("is-previewing");
      ctx.onPathPreview?.(result, btn.dataset.op);
      ctx.setStatus?.(result.message || "Path preview ready", "ok");
    } catch (err) {
      ctx.setStatus?.(err.message || String(err), "error");
    } finally {
      btn.disabled = false;
    }
  });
}

export function mountGenerateForm(root, { preview, tools, getJobId, getSettings, setStatus, onPathPreview } = {}) {
  if (!root) return;
  const copperLayer = root.querySelector("#gen-copper-layer");
  const copperTool = root.querySelector("#gen-copper-tool");
  const outlineLayer = root.querySelector("#gen-outline-layer");
  const outlineTool = root.querySelector("#gen-outline-tool");
  const drillsHost = root.querySelector("#gen-drills");
  const addBtn = root.querySelector("#gen-add-drill");

  const gerbers = gerberLayers(preview);
  const copperDefault = defaultLayer(gerbers, ["copper_top"], 0);
  const outlineDefault = defaultLayer(gerbers, ["profile"], gerbers.length ? gerbers.length - 1 : 0);

  root._previewCtx = { getJobId, getSettings, setStatus, onPathPreview };
  bindPreviewClicks(root);

  fillSelect(copperLayer, layerSelectHtml(gerbers, copperDefault?.name));
  fillSelect(copperTool, toolSelectHtml(tools, 2));
  fillSelect(outlineLayer, layerSelectHtml(gerbers, outlineDefault?.name));
  fillSelect(outlineTool, toolSelectHtml(tools, 4));

  if (drillsHost) {
    drillsHost.innerHTML = "";
    const first = renderDrillBlock(preview, tools, { extra: false });
    drillsHost.append(first);
    refreshDrillSizes(first, preview, tools);
    drillsHost._preview = preview;
    drillsHost._tools = tools;
    if (!drillsHost.dataset.bound) {
      drillsHost.dataset.bound = "1";
      drillsHost.addEventListener("change", (event) => {
        if (event.target?.name === "drill-layer") {
          refreshDrillSizes(
            event.target.closest(".gen-drill"),
            drillsHost._preview,
            drillsHost._tools
          );
        }
      });
      drillsHost.addEventListener("click", (event) => {
        const btn = event.target.closest(".gen-remove-drill");
        if (!btn) return;
        btn.closest(".gen-drill")?.remove();
      });
    }
  }

  if (addBtn) {
    addBtn.hidden = drillLayers(preview).length === 0;
    addBtn.onclick = () => {
      const extra = renderDrillBlock(preview, tools, { extra: true });
      drillsHost.append(extra);
      refreshDrillSizes(extra, preview, tools);
    };
  }
}

export function readGeneratePlan(root) {
  const copperLayer = root.querySelector("#gen-copper-layer")?.value;
  const copperTool = Number(root.querySelector("#gen-copper-tool")?.value);
  const outlineLayer = root.querySelector("#gen-outline-layer")?.value;
  const outlineTool = Number(root.querySelector("#gen-outline-tool")?.value);
  const tabCount = Number(root.querySelector("#gen-tab-count")?.value);
  const tabWidth = Number(root.querySelector("#gen-tab-width")?.value);

  const drills = [...root.querySelectorAll(".gen-drill")].map((block) => {
    const layers = [...selectedNames(block)];
    const size_map = [...block.querySelectorAll("tbody tr[data-diameter]")].map((row) => ({
      diameter_mm: Number(row.dataset.diameter),
      tool_number: Number(row.querySelector("select")?.value) || 4,
    }));
    return { layers, size_map };
  }).filter((op) => op.layers.length);

  return {
    copper: copperLayer
      ? { layer: copperLayer, tool_number: Number.isFinite(copperTool) ? copperTool : 2 }
      : null,
    drills,
    outline: outlineLayer
      ? {
          layer: outlineLayer,
          tool_number: Number.isFinite(outlineTool) ? outlineTool : 4,
          tab_count: Number.isFinite(tabCount) ? tabCount : 4,
          tab_width_mm: Number.isFinite(tabWidth) ? tabWidth : 2,
        }
      : null,
  };
}
