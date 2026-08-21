/** Upload / drag-drop helpers. */

export function setupDropzone({ dropzone, input, onFile, setStatus }) {
  const openPicker = () => input.click();

  dropzone.addEventListener("click", openPicker);
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openPicker();
    }
  });

  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) onFile(file);
    input.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setStatus("Please drop a .zip file", "error");
      return;
    }
    onFile(file);
  });
}

export async function uploadZip(file) {
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await fetch("/api/jobs/upload", { method: "POST", body });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "Upload failed");
  }
  return data;
}
