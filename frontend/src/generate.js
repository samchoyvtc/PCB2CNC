/** Student generate-plan form: contour or pocket copper, drill/pocket per hole size, outline tabs. */

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

function copperChoiceLayers(preview) {
  return (preview?.layers || []).filter(
    (layer) => layer.kind === "copper_top" || layer.kind === "copper_bottom"
  );
}

function profileChoiceLayers(preview) {
  return (preview?.layers || []).filter((layer) => layer.kind === "profile");
}

function drillLayers(preview) {
  return (preview?.layers || []).filter((layer) => layer.kind === "drill");
}

function isCornTool(tool) {
  const name = String(tool?.Name || tool?.name || "");
  const type = String(tool?.Type || tool?.type || "");
  return /corn/i.test(name) || /corn/i.test(type);
}

function cornTools(tools) {
  return (tools || []).filter(isCornTool);
}

function toolDiameter(tool) {
  const n = Number(tool?.["Diameter(D)"] ?? tool?.["Tip Diameter(F)"] ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export function defaultDrillTool(diameter, tools) {
  const candidates = cornTools(tools)
    .map((tool) => ({ number: toolNumber(tool), tip: toolTip(tool), diameter: toolDiameter(tool) }))
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

function copperLayerLabel(layer) {
  if (layer.kind === "copper_top") return `Top · ${layer.name}`;
  if (layer.kind === "copper_bottom") return `Bottom · ${layer.name}`;
  return `${layer.name} · ${layer.kind}`;
}

function profileLayerLabel(layer) {
  return `Profile · ${layer.name}`;
}

function layerSelectHtml(layers, selectedName, labelFn) {
  return layers
    .map((layer) => {
      const sel = layer.name === selectedName ? " selected" : "";
      const label = labelFn ? labelFn(layer) : `${layer.name} · ${layer.kind}`;
      return `<option value="${escapeAttr(layer.name)}" data-kind="${escapeAttr(layer.kind)}"${sel}>${escapeHtml(
        label
      )}</option>`;
    })
    .join("");
}

export function fillBoardSettingTools(tools) {
  const copperSel = document.getElementById("copper_tool_number");
  if (copperSel) {
    const current = Number(copperSel.value);
    const selected = Number.isFinite(current) && current >= 1 ? current : 2;
    fillSelect(copperSel, toolSelectHtml(tools, selected));
  }
  const outlineSel = document.getElementById("outline_tool_number");
  if (outlineSel) {
    const current = Number(outlineSel.value);
    const selected = Number.isFinite(current) && current >= 1 ? current : 4;
    fillSelect(outlineSel, toolSelectHtml(cornTools(tools), selected));
  }
}

function copperLayerIsBottom(select) {
  const opt = select?.selectedOptions?.[0];
  return (opt?.dataset?.kind || "") === "copper_bottom";
}

function syncMirrorToCopperLayer(root, select = root.querySelector(".gen-copper-layer")) {
  const mirror = root.querySelector("#gen-mirror");
  if (!mirror || !select) return false;
  const wantOn = copperLayerIsBottom(select);
  if (mirror.checked === wantOn) return false;
  mirror.checked = wantOn;
  return true;
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
    const corns = cornTools(tools);
    const chosenRaw =
      (prev && typeof prev === "object" ? prev.tool : prev) || defaultDrillTool(size.diameter, corns);
    const chosen = corns.some((tool) => toolNumber(tool) === Number(chosenRaw))
      ? Number(chosenRaw)
      : defaultDrillTool(size.diameter, corns);
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
          <select class="gen-size-tool">${toolSelectHtml(corns, chosen)}</select>
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
      const next = defaultHoleStrategy(size.diameter, Number(select.value), corns);
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

function drillDepthMm(root, fallback = 1.7) {
  const n = Number(root?._previewCtx?.getSettings?.()?.drill_depth_mm);
  return Number.isFinite(n) && n > 0 ? Math.min(20, n) : fallback;
}

function renderDrillBlock(preview) {
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
    <p class="gen-hint">Choose drill files, then a Corn mill and strategy for each hole size. Only Corn tools are listed. Drilling and cutout depth is in Board setting. Pocket mills concentric circles when the hole is larger than the tool.</p>
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

function copperMode(card) {
  return card?.querySelector(".gen-copper-mode:checked")?.value === "pocket" ? "pocket" : "contour";
}

function copperPasses(card) {
  const passes = Number(card?.querySelector(".gen-copper-passes")?.value);
  return Number.isFinite(passes) && passes >= 1 ? Math.min(12, Math.round(passes)) : 3;
}

function syncCopperModeUi(card) {
  if (!card) return;
  const mode = copperMode(card);
  const passesWrap = card.querySelector(".gen-copper-passes-wrap");
  const hint = card.querySelector(".gen-copper-hint");
  if (passesWrap) passesWrap.hidden = mode === "pocket";
  if (hint) {
    hint.textContent =
      mode === "pocket"
        ? "Clear unused copper inside the board outline, leaving traces. Tool and engraving depth are in Board setting."
        : "Contour isolation around copper. Extra passes step farther out using the tool’s step-over. Tool and engraving depth are in Board setting.";
  }
}

function copperDepthMm(settings) {
  const n = Number(settings?.engraving_depth_mm);
  return Number.isFinite(n) && n > 0 ? Math.min(5, n) : 0.2;
}

function copperToolNumber(settings) {
  const n = Number(settings?.copper_tool_number);
  return Number.isFinite(n) && n >= 1 ? n : 2;
}

function copperPlanFields(card, settings) {
  if (!card || card.hidden) return null;
  const layer = card.querySelector(".gen-copper-layer")?.value;
  if (!layer) return null;
  const mode = copperMode(card);
  const outlineLayer = card.closest("#panel-generate-wrap")?.querySelector("#gen-outline-layer")?.value;
  return {
    layer,
    tool_number: copperToolNumber(settings),
    isolation_passes: copperPasses(card),
    engrave_mode: mode,
    outline_layer: mode === "pocket" && outlineLayer && outlineLayer !== layer ? outlineLayer : null,
    depth_mm: copperDepthMm(settings),
  };
}

function fillCopperCard(card, coppers, layerName) {
  if (!card) return;
  fillSelect(card.querySelector(".gen-copper-layer"), layerSelectHtml(coppers, layerName, copperLayerLabel));
  syncCopperModeUi(card);
}

function planSettings(root) {
  return root?._previewCtx?.getSettings?.() || {};
}

function largestSelectedCorn(card, tools) {
  const selected = new Set(
    [...(card?.querySelectorAll("select.gen-size-tool") || [])].map((el) => Number(el.value))
  );
  const chosen = cornTools(tools).filter((tool) => selected.has(toolNumber(tool)));
  if (chosen.length) {
    return [...chosen].sort(
      (a, b) => toolDiameter(b) - toolDiameter(a) || toolNumber(a) - toolNumber(b)
    )[0];
  }
  return cornTools(tools).find((tool) => toolNumber(tool) === 4) || cornTools(tools)[0] || null;
}

function syncOutlineFromDrills(root) {
  const tools = root.querySelector("#gen-drills")?._tools || [];
  const drillCard = root.querySelector(".gen-drill");
  const tool = largestSelectedCorn(drillCard, tools);
  const n = toolNumber(tool);
  const hidden = root.querySelector("#gen-outline-tool");
  if (hidden && Number.isFinite(n)) hidden.value = String(n);
  const outlineSel = document.getElementById("outline_tool_number");
  if (outlineSel && Number.isFinite(n)) outlineSel.value = String(n);
  const hint = root.querySelector("#gen-outline-tool-hint");
  if (hint) {
    hint.textContent = Number.isFinite(n)
      ? `Cut uses ${toolLabel(tool)} — the largest Corn mill selected for drilling.`
      : "Cut uses a Corn mill from drilling. Select a drill tool first.";
  }
  return Number.isFinite(n) ? n : 4;
}

function planMirror(root) {
  return !!root.querySelector("#gen-mirror")?.checked;
}

function withMirror(plan, root) {
  if (!plan) return null;
  return { ...plan, mirror: planMirror(root) };
}

function planForCard(root, kind, card) {
  const settings = planSettings(root);
  if (kind === "copper") {
    const copper = copperPlanFields(card, settings);
    if (!copper) return null;
    return withMirror({ copper, drills: [], outline: null }, root);
  }
  if (kind === "drill") {
    const layers = [...selectedNames(card)];
    if (!layers.length) return null;
    const size_map = sizeMapFromCard(card);
    return withMirror(
      { copper: null, drills: [{ layers, size_map, depth_mm: drillDepthMm(root) }], outline: null },
      root
    );
  }
  if (kind === "outline") {
    const outline = outlinePlanFields(root, settings);
    if (!outline) return null;
    return withMirror({ copper: null, drills: [], outline }, root);
  }
  return null;
}

function outlineDepthMm(settings) {
  const n = Number(settings?.drill_depth_mm ?? settings?.outline_depth_mm);
  return Number.isFinite(n) && n > 0 ? Math.min(20, n) : 1.7;
}

function tabOffsetFromRoot(_root) {
  return 0;
}

function outlinePlanFields(root, settings) {
  const layer = root.querySelector("#gen-outline-layer")?.value;
  const tool = Number(root.querySelector("#gen-outline-tool")?.value) || syncOutlineFromDrills(root);
  const tabCount = Number(root.querySelector("#gen-tab-count")?.value);
  const tabWidth = Number(root.querySelector("#gen-tab-width")?.value);
  if (!layer) return null;
  return {
    layer,
    tool_number: Number.isFinite(tool) ? tool : 4,
    tab_count: Number.isFinite(tabCount) ? tabCount : 4,
    tab_width_mm: Number.isFinite(tabWidth) ? tabWidth : 2,
    tab_offset: tabOffsetFromRoot(root),
    depth_mm: outlineDepthMm(settings || planSettings(root)),
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

function previewProgressLabel(kind) {
  if (kind === "copper") return "Building copper engraving path…";
  if (kind === "drill") return "Building drill path…";
  if (kind === "outline") return "Building outline path…";
  return "Building CNC path preview…";
}

function stopPathPulse(root) {
  if (root._pathPulseId) {
    window.clearInterval(root._pathPulseId);
    root._pathPulseId = 0;
  }
}

function pulsePathProgress(root, ctx, from, to, label, meta) {
  stopPathPulse(root);
  if (!ctx.setProgress) return () => {};
  let pct = from;
  ctx.setProgress(true, pct, label, meta || `${Math.round(pct)}%`);
  const cap = Math.max(from, to - 3);
  root._pathPulseId = window.setInterval(() => {
    if (pct >= cap) return;
    pct = Math.min(cap, pct + Math.max(1.2, (cap - pct) * 0.1));
    ctx.setProgress(true, pct, label, meta || `${Math.round(pct)}%`);
  }, 180);
  return (finalPct = to, finalLabel = label) => {
    stopPathPulse(root);
    ctx.setProgress?.(true, finalPct, finalLabel, meta || `${Math.round(finalPct)}%`);
  };
}

function finishPathProgress(ctx, ok, label = "CNC paths previewed") {
  if (!ctx.setProgress) return;
  if (ok) {
    ctx.setProgress(true, 100, label, "100%");
    ctx.hideProgressSoon?.(600);
  } else {
    ctx.setProgress(false);
  }
}

function previewGen(root) {
  return root._previewGen || 0;
}

function clearAllPreviews(root) {
  root._previewGen = previewGen(root) + 1;
  stopPathPulse(root);
  const ctx = root._previewCtx || {};
  ctx.setProgress?.(false);
  ctx.clearPathPreviews?.();
  root.querySelectorAll(".gen-preview-btn").forEach((btn) => {
    const card = btn.closest(".gen-card");
    setPreviewPressed(btn, card, false);
    btn.disabled = false;
    if (!ctx.clearPathPreviews) {
      ctx.onPathPreview?.({ paths: [] }, previewKey(card, btn.dataset.op), false);
    }
  });
}

function scheduleRebuildPreviews(root) {
  window.clearTimeout(root._previewRefreshTimer);
  clearAllPreviews(root);
  const ctx = root._previewCtx || {};
  ctx.setStatus?.("Updating CNC path preview…");
  root._previewRefreshTimer = window.setTimeout(() => {
    void autoPreviewAll(root, { alreadyCleared: true });
  }, 250);
}

async function showCardPreview(root, btn, { trackProgress = true } = {}) {
  const card = btn.closest(".gen-card");
  const ctx = root._previewCtx || {};
  const kind = btn.dataset.op;
  const key = previewKey(card, kind);
  const gen = previewGen(root);
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
        : "Choose a layer to preview.",
      "error"
    );
    if (trackProgress) finishPathProgress(ctx, false);
    return;
  }
  btn.disabled = true;
  const label = previewProgressLabel(kind);
  ctx.setStatus?.(label);
  const stop = trackProgress ? pulsePathProgress(root, ctx, 8, 100, label) : null;
  try {
    const result = await previewPath(jobId, settings, plan);
    if (previewGen(root) !== gen) return;
    setPreviewPressed(btn, card, true);
    ctx.onPathPreview?.(result, key, true);
    ctx.setStatus?.(result.message || "Path preview on", "ok");
    stop?.(100, label);
    if (trackProgress) finishPathProgress(ctx, true, result.message || "Path preview on");
  } catch (err) {
    if (previewGen(root) !== gen) return;
    setPreviewPressed(btn, card, false);
    ctx.onPathPreview?.({ paths: [] }, key, false);
    ctx.setStatus?.(err.message || String(err), "error");
    stop?.(0, label);
    if (trackProgress) finishPathProgress(ctx, false);
  } finally {
    if (previewGen(root) === gen) btn.disabled = false;
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

async function runPreviewBatch(root, buttons) {
  const ctx = root._previewCtx || {};
  const gen = previewGen(root);
  const n = buttons.length;
  if (!n) return;
  let failed = false;
  for (let i = 0; i < n; i++) {
    if (previewGen(root) !== gen) return;
    const from = (i / n) * 100;
    const to = ((i + 1) / n) * 100;
    const kind = buttons[i].dataset.op;
    const label = `${previewProgressLabel(kind)} (${i + 1}/${n})`;
    const stop = pulsePathProgress(root, ctx, from, to, label);
    await showCardPreview(root, buttons[i], { trackProgress: false });
    if (previewGen(root) !== gen) return;
    const on = buttons[i].getAttribute("aria-pressed") === "true";
    if (!on) failed = true;
    stop(to, label);
  }
  if (previewGen(root) !== gen) return;
  if (failed) finishPathProgress(ctx, false);
  else {
    ctx.setStatus?.("CNC paths previewed. Press Next step · Convert when ready.", "ok");
    finishPathProgress(ctx, true, "CNC paths previewed");
  }
}

function isPlanPreviewControl(target) {
  if (!target || target.closest(".gen-preview-btn")) return false;
  if (target.id === "gen-mirror") return true;
  return !!target.closest(".gen-card");
}

function bindPreviewClicks(root) {
  if (root.dataset.previewBound) return;
  root.dataset.previewBound = "1";
  const onPlanChange = (event) => {
    const target = event.target;
    if (!isPlanPreviewControl(target)) return;
    if (target.classList.contains("gen-copper-mode")) {
      syncCopperModeUi(target.closest(".gen-copper"));
    }
    if (target.classList.contains("gen-size-tool") || target.name === "drill-layer") {
      syncOutlineFromDrills(root);
    }
    if (target.classList.contains("gen-copper-layer")) {
      root._previewCtx?.onSelectLayer?.(target.value);
      syncMirrorToCopperLayer(root, target);
    }
    scheduleRebuildPreviews(root);
    if (target.id === "gen-mirror" || target.classList.contains("gen-copper-layer")) {
      root._previewCtx?.onMirrorChange?.(!!root.querySelector("#gen-mirror")?.checked);
    }
  };
  root.addEventListener("click", async (event) => {
    const btn = event.target.closest(".gen-preview-btn");
    if (!btn || !root.contains(btn)) return;
    const on = btn.getAttribute("aria-pressed") === "true";
    if (on) hideCardPreview(root, btn);
    else await showCardPreview(root, btn);
  });
  root.addEventListener("change", onPlanChange);
  root.addEventListener("input", (event) => {
    const target = event.target;
    if (target?.type !== "number" || !target.closest(".gen-card")) return;
    onPlanChange(event);
  });
}

export async function autoPreviewAll(root, { alreadyCleared = false } = {}) {
  if (!root) return;
  if (!alreadyCleared) clearAllPreviews(root);
  const buttons = [...root.querySelectorAll(".gen-preview-btn")];
  await runPreviewBatch(root, buttons);
}

export function mountGenerateForm(root, { preview, tools, getJobId, getSettings, setStatus, setProgress, hideProgressSoon, onPathPreview, onSelectLayer, onMirrorChange, clearPathPreviews } = {}) {
  if (!root) return;
  const copperCard = root.querySelector(".gen-copper-top") || root.querySelector(".gen-copper");
  const outlineLayer = root.querySelector("#gen-outline-layer");
  const drillsHost = root.querySelector("#gen-drills");

  const coppers = copperChoiceLayers(preview);
  const copperDefault =
    coppers.find((layer) => layer.kind === "copper_top") ||
    coppers.find((layer) => layer.kind === "copper_bottom") ||
    coppers[0] ||
    null;
  const profiles = profileChoiceLayers(preview);
  const outlineDefault = profiles[0] || null;

  root._previewCtx = { getJobId, getSettings, setStatus, setProgress, hideProgressSoon, onPathPreview, onSelectLayer, onMirrorChange, clearPathPreviews };
  bindPreviewClicks(root);
  root.querySelectorAll(".gen-preview-btn").forEach((btn) => {
    setPreviewPressed(btn, btn.closest(".gen-card"), false);
  });

  fillBoardSettingTools(tools);
  fillCopperCard(copperCard, coppers, copperDefault?.name);
  syncMirrorToCopperLayer(root, copperCard?.querySelector(".gen-copper-layer"));
  onSelectLayer?.(copperCard?.querySelector(".gen-copper-layer")?.value);
  onMirrorChange?.(planMirror(root));
  fillSelect(outlineLayer, layerSelectHtml(profiles, outlineDefault?.name, profileLayerLabel));

  if (drillsHost) {
    drillsHost.innerHTML = "";
    const first = renderDrillBlock(preview);
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
          syncOutlineFromDrills(root);
        }
      });
    }
  }
  syncOutlineFromDrills(root);

  void autoPreviewAll(root);
}

export function readGeneratePlan(root) {
  const settings = planSettings(root);
  const copper = copperPlanFields(root.querySelector(".gen-copper"), settings);
  const outlineLayer = root.querySelector("#gen-outline-layer")?.value;

  const drills = [...root.querySelectorAll(".gen-drill")].map((block) => {
    const layers = [...selectedNames(block)];
    const size_map = sizeMapFromCard(block);
    return { layers, size_map, depth_mm: drillDepthMm(root) };
  }).filter((op) => op.layers.length);

  return {
    copper,
    drills,
    outline: outlineLayer ? outlinePlanFields(root, settings) : null,
    mirror: planMirror(root),
  };
}
