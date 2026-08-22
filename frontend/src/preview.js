/** Canvas board preview with zoom / pan, colored layers, and scale ruler. */

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

const LAYER_GROUPS = [
  {
    id: "top",
    title: "Top",
    kinds: ["copper_top", "silkscreen_top", "soldermask_top", "solderpaste_top"],
  },
  {
    id: "bottom",
    title: "Bottom",
    kinds: [
      "copper_bottom",
      "silkscreen_bottom",
      "soldermask_bottom",
      "solderpaste_bottom",
    ],
  },
  {
    id: "drill",
    title: "Drill",
    kinds: ["drill"],
  },
  {
    id: "profile",
    title: "Profile",
    kinds: ["profile"],
  },
];

const KIND_ORDER = {
  copper_top: 0,
  silkscreen_top: 1,
  soldermask_top: 2,
  solderpaste_top: 3,
  copper_bottom: 0,
  silkscreen_bottom: 1,
  soldermask_bottom: 2,
  solderpaste_bottom: 3,
  drill: 0,
  profile: 0,
};

function niceStep(mmPerPixel) {
  // Target ~80px for the scale bar
  const targetMm = mmPerPixel * 80;
  const candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];
  let best = candidates[0];
  for (const c of candidates) {
    best = c;
    if (c >= targetMm) break;
  }
  return best;
}

function formatMm(value) {
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, "");
  if (value >= 1) return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return value.toFixed(2);
}

export class BoardPreview {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.layers = [];
    this.drills = [];
    this.bounds = null;
    this.visibility = {};
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this._panning = false;
    this._last = null;
    this._images = {};

    this._onResize = () => this.resize();
    window.addEventListener("resize", this._onResize);
    this._bindPointer();
    this.resize();
  }

  _bindPointer() {
    const c = this.canvas;
    c.addEventListener("pointerdown", (e) => {
      this._panning = true;
      this._last = { x: e.clientX, y: e.clientY };
      c.classList.add("panning");
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (!this._panning || !this._last) return;
      const dx = e.clientX - this._last.x;
      const dy = e.clientY - this._last.y;
      this.offsetX += dx;
      this.offsetY += dy;
      this._last = { x: e.clientX, y: e.clientY };
      this.draw();
    });
    const end = (e) => {
      this._panning = false;
      this._last = null;
      c.classList.remove("panning");
      try {
        c.releasePointerCapture(e.pointerId);
      } catch (_) {
        /* ignore */
      }
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        this.zoomAt(e.offsetX, e.offsetY, factor);
      },
      { passive: false }
    );
  }

  resize() {
    const host = this.canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    const w = host.clientWidth || 600;
    const h = host.clientHeight || 420;
    this.canvas.width = Math.floor(w * dpr);
    this.canvas.height = Math.floor(h * dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  async setPreview(preview) {
    this.layers = preview.layers || [];
    this.drills = preview.drills || [];
    this.bounds = preview.bounds;
    this.visibility = {};
    this._images = {};
    for (const layer of this.layers) {
      this.visibility[layer.name] = !!layer.visible_default;
      if (layer.image_png_base64) {
        this._images[layer.name] = await loadImage(
          `data:image/png;base64,${layer.image_png_base64}`
        );
      }
    }
    this.fit();
  }

  clear() {
    this.layers = [];
    this.drills = [];
    this.bounds = null;
    this.visibility = {};
    this._images = {};
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this.draw();
  }

  setVisible(name, visible) {
    this.visibility[name] = visible;
    this.draw();
  }

  setGroupVisible(names, visible) {
    for (const name of names) {
      this.visibility[name] = visible;
    }
    this.draw();
  }

  fit() {
    const host = this.canvas.parentElement;
    const w = host.clientWidth || 600;
    const h = host.clientHeight || 420;
    if (!this.bounds) {
      this.scale = 1;
      this.offsetX = 0;
      this.offsetY = 0;
      this.draw();
      return;
    }
    // Extra pad for rulers / dimension callouts
    const pad = 56;
    const sx = (w - pad * 2) / Math.max(this.bounds.width, 0.01);
    const sy = (h - pad * 2) / Math.max(this.bounds.height, 0.01);
    this.scale = Math.min(sx, sy);
    const drawnW = this.bounds.width * this.scale;
    const drawnH = this.bounds.height * this.scale;
    this.offsetX = (w - drawnW) / 2 - this.bounds.min_x * this.scale;
    this.offsetY = (h - drawnH) / 2 + this.bounds.max_y * this.scale;
    this.draw();
  }

  zoom(factor) {
    const host = this.canvas.parentElement;
    this.zoomAt((host.clientWidth || 600) / 2, (host.clientHeight || 420) / 2, factor);
  }

  zoomAt(cx, cy, factor) {
    const worldX = (cx - this.offsetX) / this.scale;
    const worldY = (this.offsetY - cy) / this.scale;
    this.scale *= factor;
    this.offsetX = cx - worldX * this.scale;
    this.offsetY = cy + worldY * this.scale;
    this.draw();
  }

  worldToScreen(x, y) {
    return {
      x: this.offsetX + x * this.scale,
      y: this.offsetY - y * this.scale,
    };
  }

  _drillVisible() {
    const drillLayers = this.layers.filter((l) => l.kind === "drill");
    if (!drillLayers.length) return this.drills.length > 0;
    return drillLayers.some((l) => this.visibility[l.name]);
  }

  _drawScaleRuler(ctx, w, h) {
    if (!this.bounds || !(this.scale > 0)) return;

    const mmPerPx = 1 / this.scale;
    const stepMm = niceStep(mmPerPx);
    const stepPx = stepMm * this.scale;

    // Board dimension labels along the extents
    const a = this.worldToScreen(this.bounds.min_x, this.bounds.max_y);
    const b = this.worldToScreen(this.bounds.max_x, this.bounds.min_y);
    const boardW = this.bounds.width;
    const boardH = this.bounds.height;

    ctx.save();
    ctx.strokeStyle = "rgba(226, 232, 240, 0.75)";
    ctx.fillStyle = "rgba(226, 232, 240, 0.95)";
    ctx.lineWidth = 1;
    ctx.font = '600 12px "DM Sans", sans-serif';

    // Width dimension on top of board
    const midX = (a.x + b.x) / 2;
    const dimY = a.y - 18;
    ctx.beginPath();
    ctx.moveTo(a.x, dimY);
    ctx.lineTo(b.x, dimY);
    ctx.moveTo(a.x, dimY - 5);
    ctx.lineTo(a.x, dimY + 5);
    ctx.moveTo(b.x, dimY - 5);
    ctx.lineTo(b.x, dimY + 5);
    ctx.stroke();
    const wLabel = `${formatMm(boardW)} mm`;
    const wMetrics = ctx.measureText(wLabel);
    ctx.fillStyle = "rgba(7, 11, 20, 0.75)";
    ctx.fillRect(midX - wMetrics.width / 2 - 6, dimY - 16, wMetrics.width + 12, 16);
    ctx.fillStyle = "rgba(245, 166, 35, 0.98)";
    ctx.fillText(wLabel, midX - wMetrics.width / 2, dimY - 4);

    // Height dimension on left of board
    const midY = (a.y + b.y) / 2;
    const dimX = a.x - 18;
    ctx.strokeStyle = "rgba(226, 232, 240, 0.75)";
    ctx.beginPath();
    ctx.moveTo(dimX, a.y);
    ctx.lineTo(dimX, b.y);
    ctx.moveTo(dimX - 5, a.y);
    ctx.lineTo(dimX + 5, a.y);
    ctx.moveTo(dimX - 5, b.y);
    ctx.lineTo(dimX + 5, b.y);
    ctx.stroke();
    const hLabel = `${formatMm(boardH)} mm`;
    ctx.save();
    ctx.translate(dimX - 8, midY);
    ctx.rotate(-Math.PI / 2);
    const hMetrics = ctx.measureText(hLabel);
    ctx.fillStyle = "rgba(7, 11, 20, 0.75)";
    ctx.fillRect(-hMetrics.width / 2 - 6, -14, hMetrics.width + 12, 16);
    ctx.fillStyle = "rgba(56, 189, 248, 0.98)";
    ctx.fillText(hLabel, -hMetrics.width / 2, -2);
    ctx.restore();

    // Bottom-left scale bar (zoom reference)
    const margin = 16;
    const barY = h - margin - 10;
    const barX = margin;
    const barLen = stepPx;
    ctx.fillStyle = "rgba(7, 11, 20, 0.72)";
    ctx.fillRect(barX - 8, barY - 22, barLen + 70, 36);
    ctx.strokeStyle = "rgba(226, 232, 240, 0.9)";
    ctx.beginPath();
    ctx.moveTo(barX, barY);
    ctx.lineTo(barX + barLen, barY);
    ctx.moveTo(barX, barY - 6);
    ctx.lineTo(barX, barY + 6);
    ctx.moveTo(barX + barLen, barY - 6);
    ctx.lineTo(barX + barLen, barY + 6);
    // minor ticks
    const minors = 5;
    for (let i = 1; i < minors; i++) {
      const x = barX + (barLen * i) / minors;
      ctx.moveTo(x, barY - 3);
      ctx.lineTo(x, barY + 3);
    }
    ctx.stroke();
    ctx.fillStyle = "rgba(226, 232, 240, 0.95)";
    ctx.font = '500 11px "JetBrains Mono", monospace';
    ctx.fillText(`${formatMm(stepMm)} mm`, barX + barLen + 8, barY + 4);

    // Board size summary chip
    const summary = `Board ${formatMm(boardW)} × ${formatMm(boardH)} mm`;
    ctx.font = '600 12px "DM Sans", sans-serif';
    const sMetrics = ctx.measureText(summary);
    const sx = w - margin - sMetrics.width - 16;
    const sy = margin;
    ctx.fillStyle = "rgba(7, 11, 20, 0.78)";
    ctx.fillRect(sx, sy, sMetrics.width + 16, 26);
    ctx.strokeStyle = "rgba(42, 54, 80, 0.9)";
    ctx.strokeRect(sx, sy, sMetrics.width + 16, 26);
    ctx.fillStyle = "rgba(232, 238, 249, 0.95)";
    ctx.fillText(summary, sx + 8, sy + 17);

    ctx.restore();
  }

  draw() {
    const host = this.canvas.parentElement;
    const w = host.clientWidth || 600;
    const h = host.clientHeight || 420;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);

    // Draw Gerber layer images aligned to their own bounds
    for (const layer of this.layers) {
      if (!this.visibility[layer.name]) continue;
      const img = this._images[layer.name];
      if (!img || !layer.bounds) continue;
      const b = layer.bounds;
      const p0 = this.worldToScreen(b.min_x, b.max_y);
      const p1 = this.worldToScreen(b.max_x, b.min_y);
      ctx.drawImage(img, p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
    }

    // Drill hits — any visible drill group member enables overlay
    if (this._drillVisible()) {
      for (const hit of this.drills) {
        const p = this.worldToScreen(hit.x, hit.y);
        const r = Math.max(2, (hit.diameter * this.scale) / 2);
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(239, 68, 68, 0.85)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.5)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    // Bounds frame
    if (this.bounds) {
      const a = this.worldToScreen(this.bounds.min_x, this.bounds.max_y);
      const b = this.worldToScreen(this.bounds.max_x, this.bounds.min_y);
      ctx.strokeStyle = "rgba(148, 163, 184, 0.45)";
      ctx.lineWidth = 1;
      ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    }

    this._drawScaleRuler(ctx, w, h);
  }
}

function kindLabel(kind) {
  return kind.replace(/_/g, " ");
}

function collectDrillRows(members) {
  const map = new Map();
  for (const layer of members) {
    for (const tool of layer.drill_tools || []) {
      const dia = Number(tool.diameter);
      const key = `${tool.tool}|${dia.toFixed(3)}`;
      const prev = map.get(key) || {
        tool: tool.tool || "T?",
        diameter: dia,
        qty: 0,
        file: layer.name,
      };
      prev.qty += Number(tool.count) || 0;
      map.set(key, prev);
    }
  }
  return [...map.values()].sort(
    (a, b) => a.diameter - b.diameter || String(a.tool).localeCompare(String(b.tool))
  );
}

export function renderLayerToggles(
  container,
  layers,
  onToggle,
  onGroupToggle,
  drills = []
) {
  container.innerHTML = "";

  const used = new Set();
  const groups = [];

  for (const def of LAYER_GROUPS) {
    const members = layers
      .filter((l) => def.kinds.includes(l.kind))
      .sort(
        (a, b) =>
          (KIND_ORDER[a.kind] ?? 99) - (KIND_ORDER[b.kind] ?? 99) ||
          a.name.localeCompare(b.name)
      );
    if (!members.length) continue;
    members.forEach((m) => used.add(m.name));
    groups.push({ ...def, members });
  }

  const other = layers.filter((l) => !used.has(l.name) && l.kind !== "bom");
  if (other.length) {
    groups.push({ id: "other", title: "Other", kinds: [], members: other });
  }

  for (const group of groups) {
    const details = document.createElement("details");
    details.className = "layer-group";
    details.open = true;
    details.dataset.group = group.id;

    const summary = document.createElement("summary");
    summary.className = "layer-group-summary";

    const groupCb = document.createElement("input");
    groupCb.type = "checkbox";
    groupCb.className = "group-toggle";
    const allOn = group.members.every((m) => m.visible_default);
    const someOn = group.members.some((m) => m.visible_default);
    groupCb.checked = allOn;
    groupCb.indeterminate = !allOn && someOn;
    groupCb.title = `Toggle all ${group.title} layers`;
    groupCb.addEventListener("click", (e) => e.stopPropagation());
    groupCb.addEventListener("change", () => {
      const names = group.members.map((m) => m.name);
      const visible = groupCb.checked;
      for (const name of names) {
        const cb = details.querySelector(`input[data-layer="${CSS.escape(name)}"]`);
        if (cb) cb.checked = visible;
      }
      groupCb.indeterminate = false;
      if (onGroupToggle) onGroupToggle(names, visible);
      else names.forEach((n) => onToggle(n, visible));
    });

    const title = document.createElement("span");
    title.className = "layer-group-title";
    title.textContent = `${group.title}`;
    const count = document.createElement("span");
    count.className = "layer-group-count";
    if (group.id === "drill") {
      const holes = collectDrillRows(group.members).reduce((n, r) => n + r.qty, 0);
      count.textContent = holes ? String(holes) : String(group.members.length);
    } else {
      count.textContent = String(group.members.length);
    }

    summary.append(groupCb, title, count);
    details.append(summary);

    const list = document.createElement("ul");
    list.className = "layer-group-list";

    for (const layer of group.members) {
      const li = document.createElement("li");
      li.className = "layer-row";
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!layer.visible_default;
      cb.dataset.layer = layer.name;
      cb.addEventListener("change", () => {
        onToggle(layer.name, cb.checked);
        const memberCbs = [...list.querySelectorAll("input[data-layer]")];
        const checked = memberCbs.filter((c) => c.checked).length;
        groupCb.checked = checked === memberCbs.length;
        groupCb.indeterminate = checked > 0 && checked < memberCbs.length;
      });
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = layer.color;
      const text = document.createElement("span");
      text.className = "layer-name";
      text.textContent =
        group.id === "drill" ? layer.name : `${kindLabel(layer.kind)} · ${layer.name}`;
      if (layer.error) {
        text.textContent += " (error)";
        text.style.color = "#ef4444";
      }
      label.append(cb, swatch, text);
      li.append(label);
      list.append(li);
    }

    if (group.id === "drill") {
      const rows = collectDrillRows(group.members);
      if (rows.length) {
        const wrap = document.createElement("div");
        wrap.className = "drill-table-wrap";
        const table = document.createElement("table");
        table.className = "drill-table";
        table.innerHTML =
          "<thead><tr><th>Tool</th><th>Drill size</th><th>Qty</th></tr></thead>";
        const tbody = document.createElement("tbody");
        for (const row of rows) {
          const tr = document.createElement("tr");
          tr.innerHTML =
            `<td>${row.tool}</td>` +
            `<td>Ø ${row.diameter.toFixed(3)} mm</td>` +
            `<td>${row.qty}</td>`;
          tbody.append(tr);
        }
        table.append(tbody);
        wrap.append(table);
        list.append(wrap);
      }
    }

    details.append(list);
    container.append(details);
  }

  void drills;
}
