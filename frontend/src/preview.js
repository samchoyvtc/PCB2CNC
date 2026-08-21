/** Canvas board preview with zoom / pan and colored layers. */

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
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

  setVisible(name, visible) {
    this.visibility[name] = visible;
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
    const pad = 40;
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

    // Drill hits
    const drillLayer = this.layers.find((l) => l.kind === "drill");
    const showDrills = !drillLayer || this.visibility[drillLayer.name];
    if (showDrills) {
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
  }
}

export function renderLayerToggles(container, layers, onToggle) {
  container.innerHTML = "";
  for (const layer of layers) {
    const li = document.createElement("li");
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!layer.visible_default;
    cb.addEventListener("change", () => onToggle(layer.name, cb.checked));
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = layer.color;
    const text = document.createElement("span");
    text.textContent = `${layer.kind} · ${layer.name}`;
    if (layer.error) {
      text.textContent += " (error)";
      text.style.color = "#ef4444";
    }
    label.append(cb, swatch, text);
    li.append(label);
    container.append(li);
  }
}
