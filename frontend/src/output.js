import { drillToolColor } from "./preview.js";

/** Generation + download UI. */

export async function generateJob(jobId, settings, plan) {
  const res = await fetch(`/api/jobs/${jobId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings, plan }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg || item).join("; ")
          : "Generation failed";
    throw new Error(message);
  }
  return data;
}

export async function previewPath(jobId, settings, plan) {
  const res = await fetch(`/api/jobs/${jobId}/preview-path`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings, plan }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "Path preview failed");
  }
  return data;
}

export function ncFileLabel(name) {
  if (name === "all.nc") return "Combined job";
  if (String(name).includes("isolation_bottom")) return "Copper bottom engraving";
  if (String(name).startsWith("isolation")) return "Copper engraving";
  if (String(name).startsWith("drill")) return "Drilling";
  if (String(name).startsWith("outline")) return "Board outline";
  return "G-code";
}

export function sortNcNames(files) {
  return [...(files || [])].sort((a, b) => {
    const rank = (name) =>
      name === "all.nc" ? 0 : name.startsWith("isolation") ? 1 : name.startsWith("drill") ? 2 : 3;
    return rank(a) - rank(b) || a.localeCompare(b);
  });
}

export async function fetchNcText(jobId, name) {
  const res = await fetch(`/api/jobs/${jobId}/nc/${encodeURIComponent(name)}`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Failed to load G-code");
  }
  return res.text();
}

const SKIP_TITLE = /^(material:|board:|drill depth:|clearance height:|retract height:|combined |merged |coolant|return tool|home position)/i;

function jobFromPart(fileName, title, hole) {
  const name = String(fileName || "").toLowerCase();
  const heading = String(title || "").trim();
  if (name.startsWith("isolation") || /engrav/i.test(heading)) {
    const bottom = name.includes("bottom") || /bottom/i.test(heading);
    return {
      job: bottom ? "Copper bottom engraving" : "Copper engraving",
      detail: /pocket/i.test(heading) ? "Pocket" : "Isolation",
    };
  }
  if (name.startsWith("drill") || hole || /drill/i.test(heading)) {
    if (hole) {
      const holes = hole.count === 1 ? "hole" : "holes";
      return {
        job: "Drilling",
        detail: `Ø ${hole.diameter} mm · ${hole.mode} · ${hole.count} ${holes}`,
      };
    }
    return { job: "Drilling", detail: "Holes" };
  }
  if (name.startsWith("outline") || /outline/i.test(heading)) {
    return { job: "Board outline", detail: "Outside cut" };
  }
  return { job: heading || fileName || "Job", detail: "" };
}

function toolRecord(tools, number) {
  return (tools || []).find((tool) => {
    const n = Number(tool?.Number ?? tool?.number ?? tool?.tool);
    return n === Number(number);
  });
}

export function parseNcJobSequence(text) {
  const seq = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    const marked = line.match(/^;\s*SEQ\s+(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*T(\d+)\s*$/i);
    if (marked) {
      seq.push({
        step: Number(marked[1]),
        job: marked[2].trim(),
        detail: marked[3].trim(),
        tool: Number(marked[4]),
      });
      continue;
    }
    const two = line.match(/^;\s*SEQ\s+(\d+)\s*\|\s*(.*?)\s*\|\s*T(\d+)\s*$/i);
    if (two) {
      seq.push({
        step: Number(two[1]),
        job: two[2].trim(),
        detail: "",
        tool: Number(two[3]),
      });
    }
  }
  if (seq.length) return expandToolChanges(seq);

  const rows = [];
  let currentFile = "";
  let title = "";
  let hole = null;
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    const begin = line.match(/^;\s*Begin\s+(\S+)/i);
    if (begin) {
      currentFile = begin[1];
      title = "";
      hole = null;
      continue;
    }
    if (/^;\s*End\s+/i.test(line)) {
      currentFile = "";
      title = "";
      hole = null;
      continue;
    }
    const holeMatch = line.match(
      /^;\s*T(\d+)\s+holes\s+[Øø]\s*([\d.]+)\s*mm\s+(\w+)\s*\((\d+)\)/i
    );
    if (holeMatch) {
      hole = {
        diameter: holeMatch[2],
        mode: holeMatch[3].toLowerCase(),
        count: Number(holeMatch[4]),
      };
      continue;
    }
    if (line.startsWith(";")) {
      let inner = line.replace(/^;\s*/, "").replace(/^---\s*/, "").replace(/\s*---$/, "").trim();
      if (inner && !SKIP_TITLE.test(inner) && !/^SEQ\s/i.test(inner)) title = inner;
      continue;
    }
    const toolMatch = line.match(/^T(\d+)\s*M6\b/i);
    if (!toolMatch) continue;
    const tool = Number(toolMatch[1]);
    if (tool === 0) continue;
    const { job, detail } = jobFromPart(currentFile, title, hole);
    rows.push({
      step: rows.length + 1,
      job,
      detail,
      tool,
      file: currentFile,
    });
    hole = null;
  }
  return expandToolChanges(rows);
}

function expandToolChanges(steps) {
  if (!steps.length) return steps;
  if (steps.some((step) => /^tool change$/i.test(step.job))) {
    return steps.map((step, index) => ({ ...step, step: index + 1 }));
  }
  const out = [];
  let previous = null;
  for (const step of steps) {
    const tool = Number(step.tool);
    if (previous == null) {
      out.push({ job: "Tool change", detail: `Load T${tool}`, tool });
    } else if (tool !== previous) {
      out.push({ job: "Tool change", detail: `T${previous} → T${tool}`, tool });
    }
    out.push(step);
    previous = tool;
  }
  return out.map((step, index) => ({ ...step, step: index + 1 }));
}

export function renderJobSequence(container, steps, tools) {
  container.replaceChildren();
  for (const step of steps) {
    const tr = document.createElement("tr");
    if (/^tool change$/i.test(step.job)) tr.classList.add("is-wait");

    const seq = document.createElement("td");
    seq.className = "seq";
    seq.textContent = String(step.step);

    const job = document.createElement("td");
    job.className = "job";
    const jobName = document.createElement("span");
    jobName.className = "job-name";
    jobName.textContent = step.job;
    job.append(jobName);
    if (step.detail) {
      const detail = document.createElement("span");
      detail.className = "job-detail";
      detail.textContent = step.detail;
      job.append(detail);
    }

    const tool = document.createElement("td");
    tool.className = "tool";
    const toolRow = document.createElement("span");
    toolRow.className = "tool-row";
    const swatch = document.createElement("span");
    swatch.className = "gen-tool-swatch";
    swatch.style.background = drillToolColor(step.tool);
    const num = document.createElement("span");
    num.className = "tool-num";
    num.textContent = `T${step.tool}`;
    toolRow.append(swatch, num);
    tool.append(toolRow);
    const record = toolRecord(tools, step.tool);
    const toolName = record?.Name || record?.name;
    if (toolName) {
      const name = document.createElement("span");
      name.className = "tool-name";
      name.textContent = String(toolName);
      tool.append(name);
    }

    tr.append(seq, job, tool);
    container.append(tr);
  }
}

export function renderDownloads(container, jobId, files, options = {}) {
  container.replaceChildren();
  const names = sortNcNames(files);
  const selected = options.selected || names[0] || "";
  for (const name of names) {
    const li = document.createElement("li");
    li.dataset.name = name;
    li.tabIndex = 0;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", name === selected ? "true" : "false");
    if (name === selected) li.classList.add("is-selected");
    const copy = document.createElement("div");
    copy.className = "file-copy";
    const fileName = document.createElement("span");
    fileName.className = "file-name";
    fileName.textContent = name;
    const fileMeta = document.createElement("span");
    fileMeta.className = "file-meta";
    fileMeta.textContent = ncFileLabel(name);
    copy.append(fileName, fileMeta);
    const a = document.createElement("a");
    a.href = `/api/jobs/${jobId}/nc/${encodeURIComponent(name)}`;
    a.textContent = "Download";
    a.download = name;
    a.addEventListener("click", (event) => event.stopPropagation());
    li.append(copy, a);
    li.addEventListener("click", () => options.onSelect?.(name));
    li.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      options.onSelect?.(name);
    });
    container.append(li);
  }
}
