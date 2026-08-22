/** Generation + download UI. */

export async function generateJob(jobId, settings) {
  const res = await fetch(`/api/jobs/${jobId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "Generation failed");
  }
  return data;
}

export function renderDownloads(container, jobId, files) {
  container.innerHTML = "";
  for (const name of files) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `/api/jobs/${jobId}/nc/${encodeURIComponent(name)}`;
    a.textContent = name;
    a.download = name;
    li.append(a);
    container.append(li);
  }
}

export function showToolpathPreview(imgEl, base64) {
  if (!base64) {
    imgEl.classList.remove("visible");
    imgEl.removeAttribute("src");
    return;
  }
  imgEl.src = `data:image/png;base64,${base64}`;
  imgEl.classList.add("visible");
}
