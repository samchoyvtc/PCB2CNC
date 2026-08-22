/** Stage 3 generate-plan form: copper, drills, outline + tabs. */

import { previewPath } from "./output.js";
import { drillToolColor } from "./preview.js";

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
  candidates.sort(
    (a, b) =>
      Math.abs(a.tip - diameter) - Math.abs(b.tip - diameter) ||
      b.tip - a.tip ||
      a.number - b.number
  );
  return candidates[0].number;
}

export function defaultHoleStrategy(diameter, toolNum, tools) {
  const tool = (tools || []).find((item) => toolNumber(item) === Number(toolNum));
  const tip = tool ? toolTip(tool) : 0;
  if (!(tip > 0)) return "drill";
  return diameter > tip + 0.05 ? "pocket" : "drill";
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

function existingSizeState(block) {
  const out = new Map();
  block.querySelectorAll("tbody tr[data-diameter]").forEach((row) => {
    const select = row.querySelector("select.gen-size-tool");
    if (!select) return;
    const strategy =
      row.querySelector(".gen-size-strategy:checked")?.value === "pocket" ? "pocket" : "drill";
    out.set(row.dataset.diameter, { tool: Number(select.value), strategy });
  });
  return out;
}

function sizeMapFromCard(card) {
  return [...card.querySelectorAll("tbody tr[data-diameter]")].map((row) => ({
    diameter_mm: Number(row.dataset.diameter),
    tool_number: Number(row.querySelector("select.gen-size-tool")?.value) || 4,
    strategy:
      row.querySelector(".gen-size-strategy:checked")?.value === "pocket" ? "pocket" : "drill",
  }));
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
    const prev = previous.get(key);
    const chosen =
      (prev && typeof prev === "object" ? prev.tool : prev) || defaultDrillTool(size.diameter, tools);
    const strategy =
      prev && typeof prev === "object" && prev.strategy
        ? prev.strategy
        : defaultHoleStrategy(size.diameter, chosen, tools);
    const tr = document.createElement("tr");
    tr.dataset.diameter = key;
    const group = `hole-strategy-${block.dataset.previewId || "drill"}-${key.replace(".", "_")}`;
    tr.innerHTML = `
      <td>${size.diameter.toFixed(3)} mm</td>
      <td>${size.count}</td>
      <td>
        <div class="gen-size-tool-cell">
          <span class="gen-tool-swatch" aria-hidden="true"></span>
          <select class="gen-size-tool">${toolSelectHtml(tools, chosen)}</select>
        </div>
      </td>
      <td>
        <div class="gen-seg gen-size-strategy-seg" role="radiogroup" aria-label="Hole strategy">
          <label class="gen-seg-opt">
            <input type="radio" class="gen-size-strategy" name="${group}" value="drill"${
              strategy === "drill" ? " checked" : ""
            } />
            Drill
          </label>
          <label class="gen-seg-opt">
            <input type="radio" class="gen-size-strategy" name="${group}" value="pocket"${
              strategy === "pocket" ? " checked" : ""
            } />
            Pocket
          </label>
        </div>
      </td>
    `;
    const select = tr.querySelector("select.gen-size-tool");
    const swatch = tr.querySelector(".gen-tool-swatch");
    const paint = () => {
      swatch.style.background = drillToolColor(select.value);
    };
    const syncStrategy = () => {
      const next = defaultHoleStrategy(size.diameter, Number(select.value), tools);
      const radio = tr.querySelector(`.gen-size-strategy[value="${next}"]`);
      if (radio) radio.checked = true;
    };
    select.addEventListener("change", () => {
      paint();
      syncStrategy();
    });
    paint();
    tbody.append(tr);
  }
}

function refreshDrillSizes(block, preview, tools) {
  const sizes = sizesFromLayers(drillLayers(preview), selectedNames(block));
  renderSizeRows(block, sizes, tools, existingSizeState(block));
}

function drillDepthMm(card, fallback = 1.6) {
  const n = Number(card.querySelector(".gen-drill-depth")?.value);
  return Number.isFinite(n) && n > 0 ? Math.min(20, n) : fallback;
}

function renderDrillBlock(preview, tools, { depthMm = 1.6 } = {}) {
  const drills = drillLayers(preview);
  const section = document.createElement("section");
  section.className = "gen-card gen-drill";
  section.dataset.previewId = "drill";
  const checks = drills
    .map((layer) => {
      const checked = " checked";
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
      <h3>2 · PCB drilling</h3>
      <div class="gen-card-actions">
        <button type="button" class="gen-preview-btn" data-op="drill" aria-pressed="false">Preview</button>
      </div>
    </div>
    <p class="gen-hint">Choose one or more drill files, then assign a tool and strategy to each hole size. Pocket mills a circle when the hole is larger than the tool. Preview colours match the tool.</p>
    <div class="field">
      <label>Drill depth (mm)</label>
      <input class="gen-drill-depth" type="number" min="0.01" max="20" step="0.01" value="${Number(depthMm) || 1.6}" />
    </div>
    <div class="gen-checks">${checks || '<p class="gen-hint">No drill files in this zip.</p>'}</div>
    <div class="gen-size-wrap">
      <table class="gen-size-table">
        <thead>
          <tr><th>Hole Ø</th><th>Count</th><th>Tool</th><th>Strategy</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <p class="gen-size-empty" hidden>Select a drill file to map hole sizes.</p>
    </div>
  `;
  return section;
}

function copperMode(root) {
  return root.querySelector('input[name="gen-copper-mode"]:checked')?.value === "pocket"
    ? "pocket"
    : "contour";
}

function copperPasses(root) {
  const passes = Number(root.querySelector("#gen-copper-passes")?.value);
  return Number.isFinite(passes) && passes >= 1 ? Math.min(12, Math.round(passes)) : 1;
}

function copperDepthMm(root) {
  const n = Number(root.querySelector("#gen-copper-depth")?.value);
  return Number.isFinite(n) && n > 0 ? Math.min(5, n) : 0.15;
}

function copperPlanFields(root) {
  const layer = root.querySelector("#gen-copper-layer")?.value;
  const tool = Number(root.querySelector("#gen-copper-tool")?.value);
  const mode = copperMode(root);
  const outlineLayer = root.querySelector("#gen-copper-outline")?.value || null;
  if (!layer) return null;
  return {
    layer,
    tool_number: Number.isFinite(tool) ? tool : 2,
    isolation_passes: copperPasses(root),
    engrave_mode: mode,
    outline_layer: mode === "pocket" ? outlineLayer : null,
    depth_mm: copperDepthMm(root),
  };
}

function syncCopperModeUi(root) {
  const mode = copperMode(root);
  const passesWrap = root.querySelector("#gen-copper-passes-wrap");
  const outlineWrap = root.querySelector("#gen-copper-outline-wrap");
  const hint = root.querySelector("#gen-copper-hint");
  if (passesWrap) passesWrap.hidden = mode === "pocket";
  if (outlineWrap) outlineWrap.hidden = mode !== "pocket";
  if (hint) {
    hint.textContent =
      mode === "pocket"
        ? "Clear unused copper inside the selected board outline, leaving traces. Step-over comes from the selected tool’s PCB row."
        : "Contour isolation around copper. Extra passes step farther out using the tool’s step-over.";
  }
}

function planForCard(root, kind, card) {
  if (kind === "copper") {
    const copper = copperPlanFields(root);
    if (!copper) return null;
    if (copper.engrave_mode === "pocket" && !copper.outline_layer) return null;
    return {
      copper,
      drills: [],
      outline: null,
    };
  }
  if (kind === "drill") {
    const layers = [...selectedNames(card)];
    if (!layers.length) return null;
    const size_map = sizeMapFromCard(card);
    return { copper: null, drills: [{ layers, size_map, depth_mm: drillDepthMm(card) }], outline: null };
  }
  if (kind === "outline") {
    const outline = outlinePlanFields(root);
    if (!outline) return null;
    return { copper: null, drills: [], outline };
  }
  return null;
}

function outlineDepthMm(root) {
  const n = Number(root.querySelector("#gen-outline-depth")?.value);
  return Number.isFinite(n) && n > 0 ? Math.min(20, n) : 1.6;
}

function wrapOffset(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return ((n % 1) + 1) % 1;
}

function tabOffsetFromRoot(root) {
  return wrapOffset(root.querySelector("#gen-tab-offset")?.value);
}

function setTabOffsetUi(root, offset) {
  const wrapped = wrapOffset(offset);
  const el = root.querySelector("#gen-tab-offset");
  const out = root.querySelector("#gen-tab-offset-readout");
  if (el) el.value = String(wrapped);
  if (out) out.textContent = `${Math.round(wrapped * 100)}%`;
}

function syncTabOffsetVisibility(root) {
  const wrap = root.querySelector("#gen-tab-offset-wrap");
  const count = Number(root.querySelector("#gen-tab-count")?.value);
  if (wrap) wrap.hidden = !(count > 0);
}

function outlinePlanFields(root) {
  const layer = root.querySelector("#gen-outline-layer")?.value;
  const tool = Number(root.querySelector("#gen-outline-tool")?.value);
  const tabCount = Number(root.querySelector("#gen-tab-count")?.value);
  const tabWidth = Number(root.querySelector("#gen-tab-width")?.value);
  if (!layer) return null;
  return {
    layer,
    tool_number: Number.isFinite(tool) ? tool : 4,
    tab_count: Number.isFinite(tabCount) ? tabCount : 4,
    tab_width_mm: Number.isFinite(tabWidth) ? tabWidth : 2,
    tab_offset: tabOffsetFromRoot(root),
    depth_mm: outlineDepthMm(root),
  };
}

function previewKey(card, kind) {
  return card?.dataset.previewId || kind;
}

function setPreviewPressed(btn, card, on) {
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Preview on" : "Preview";
  card?.classList.toggle("is-previewing", on);
}

async function showCardPreview(root, btn) {
  const card = btn.closest(".gen-card");
  const ctx = root._previewCtx || {};
  const kind = btn.dataset.op;
  const key = previewKey(card, kind);
  const jobId = ctx.getJobId?.();
  const settings = ctx.getSettings?.();
  const plan = planForCard(root, kind, card);
  if (!jobId) {
    ctx.setStatus?.("Upload a Gerber zip first.", "error");
    return;
  }
  if (!plan) {
    setPreviewPressed(btn, card, false);
    ctx.onPathPreview?.({ paths: [] }, key, false);
    ctx.setStatus?.(
      kind === "drill"
        ? "Select at least one drill file to preview."
        : kind === "copper" && copperMode(root) === "pocket"
          ? "Choose a board outline layer to pocket."
          : "Choose a layer to preview.",
      "error"
    );
    return;
  }
  btn.disabled = true;
  ctx.setStatus?.("Building CNC path preview…");
  try {
    const result = await previewPath(jobId, settings, plan);
    setPreviewPressed(btn, card, true);
    ctx.onPathPreview?.(result, key, true);
    ctx.setStatus?.(result.message || "Path preview on", "ok");
  } catch (err) {
    setPreviewPressed(btn, card, false);
    ctx.onPathPreview?.({ paths: [] }, key, false);
    ctx.setStatus?.(err.message || String(err), "error");
  } finally {
    btn.disabled = false;
  }
}

function hideCardPreview(root, btn) {
  const card = btn.closest(".gen-card");
  const ctx = root._previewCtx || {};
  const key = previewKey(card, btn.dataset.op);
  setPreviewPressed(btn, card, false);
  ctx.onPathPreview?.({ paths: [] }, key, false);
  ctx.setStatus?.("Path preview off", "ok");
}

function bindPreviewClicks(root) {
  if (root.dataset.previewBound) return;
  root.dataset.previewBound = "1";
  let refreshTimer = 0;
  root.addEventListener("click", async (event) => {
    const btn = event.target.closest(".gen-preview-btn");
    if (!btn || !root.contains(btn)) return;
    const on = btn.getAttribute("aria-pressed") === "true";
    if (on) hideCardPreview(root, btn);
    else await showCardPreview(root, btn);
  });
  root.addEventListener("change", (event) => {
    const card = event.target.closest(".gen-card");
    const btn = card?.querySelector(".gen-preview-btn");
    if (!btn || btn.getAttribute("aria-pressed") !== "true") return;
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => showCardPreview(root, btn), 250);
  });
}

export function mountGenerateForm(root, { preview, tools, getJobId, getSettings, setStatus, onPathPreview } = {}) {
  if (!root) return;
  const copperLayer = root.querySelector("#gen-copper-layer");
  const copperTool = root.querySelector("#gen-copper-tool");
  const copperOutline = root.querySelector("#gen-copper-outline");
  const outlineLayer = root.querySelector("#gen-outline-layer");
  const outlineTool = root.querySelector("#gen-outline-tool");
  const drillsHost = root.querySelector("#gen-drills");

  const gerbers = gerberLayers(preview);
  const copperDefault = defaultLayer(gerbers, ["copper_top"], 0);
  const outlineDefault = defaultLayer(gerbers, ["profile"], gerbers.length ? gerbers.length - 1 : 0);

  root._previewCtx = { getJobId, getSettings, setStatus, onPathPreview };
  bindPreviewClicks(root);
  root.querySelectorAll(".gen-preview-btn").forEach((btn) => {
    setPreviewPressed(btn, btn.closest(".gen-card"), false);
  });

  fillSelect(copperLayer, layerSelectHtml(gerbers, copperDefault?.name));
  fillSelect(copperTool, toolSelectHtml(tools, 2));
  fillSelect(copperOutline, layerSelectHtml(gerbers, outlineDefault?.name));
  fillSelect(outlineLayer, layerSelectHtml(gerbers, outlineDefault?.name));
  fillSelect(outlineTool, toolSelectHtml(tools, 4));
  const copperDepth = root.querySelector("#gen-copper-depth");
  const outlineDepth = root.querySelector("#gen-outline-depth");
  const settings = getSettings?.() || {};
  if (copperDepth) {
    const fromSettings = Number(settings.engraving_depth_mm);
    if (Number.isFinite(fromSettings) && fromSettings > 0) {
      copperDepth.value = String(fromSettings);
    }
  }
  if (outlineDepth) {
    const fromSettings = Number(settings.drill_depth_mm);
    if (Number.isFinite(fromSettings) && fromSettings > 0) {
      outlineDepth.value = String(fromSettings);
    }
  }
  syncCopperModeUi(root);
  syncTabOffsetVisibility(root);
  setTabOffsetUi(root, tabOffsetFromRoot(root));
  if (!root.dataset.copperModeBound) {
    root.dataset.copperModeBound = "1";
    root.addEventListener("change", (event) => {
      if (event.target?.name === "gen-copper-mode") syncCopperModeUi(root);
    });
  }
  if (!root.dataset.tabOffsetBound) {
    root.dataset.tabOffsetBound = "1";
    root.addEventListener("input", (event) => {
      const id = event.target?.id;
      if (id === "gen-tab-offset") {
        setTabOffsetUi(root, tabOffsetFromRoot(root));
      }
      if (id === "gen-tab-count") {
        syncTabOffsetVisibility(root);
      }
    });
  }

  if (drillsHost) {
    drillsHost.innerHTML = "";
    const depthMm = getSettings?.()?.drill_depth_mm ?? 1.6;
    const first = renderDrillBlock(preview, tools, { depthMm });
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
    }
  }
}

export function readGeneratePlan(root) {
  const copper = copperPlanFields(root);
  const outlineLayer = root.querySelector("#gen-outline-layer")?.value;

  const drills = [...root.querySelectorAll(".gen-drill")].map((block) => {
    const layers = [...selectedNames(block)];
    const size_map = sizeMapFromCard(block);
    return { layers, size_map, depth_mm: drillDepthMm(block) };
  }).filter((op) => op.layers.length);

  return {
    copper,
    drills,
    outline: outlineLayer ? outlinePlanFields(root) : null,
  };
}
