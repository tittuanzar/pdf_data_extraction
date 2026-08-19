(() => {
  const ENDPOINT = "/api/extract-Tag-wise-Enquiry-pdf";
  const $ = (s) => document.querySelector(s);

  const dropzone   = $("#dropzone");
  const dzEmpty    = $("#dropzone-empty");
  const dzFile     = $("#dropzone-file");
  const fileInput  = $("#file-input");
  const fileName   = $("#file-name");
  const fileMeta   = $("#file-meta");
  const clearBtn   = $("#clear-btn");
  const extractBtn = $("#extract-btn");

  const stProgress = $("#state-progress");
  const stError    = $("#state-error");
  const stDone     = $("#state-done");

  const progTitle = $("#prog-title");
  const progSub   = $("#prog-sub");
  const barFill   = $("#bar-fill");
  const errMsg    = $("#err-msg");
  const retryBtn  = $("#retry-btn");
  const doneMsg   = $("#done-msg");
  const dlBtn     = $("#download-btn");

  let file = null;

  /* helpers */
  const fmt = (b) => b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB";
  const hide = () => { stProgress.classList.add("hidden"); stError.classList.add("hidden"); stDone.classList.add("hidden"); };
  const show = (el) => { hide(); el.classList.remove("hidden"); };

  function pick(f) {
    if (!f || !f.name.toLowerCase().endsWith(".pdf")) return;
    file = f;
    fileName.textContent = f.name;
    fileMeta.textContent = fmt(f.size);
    dzEmpty.classList.add("hidden");
    dzFile.classList.remove("hidden");
    extractBtn.disabled = false;
    hide();
  }

  function reset() {
    file = null;
    fileInput.value = "";
    dzEmpty.classList.remove("hidden");
    dzFile.classList.add("hidden");
    extractBtn.disabled = true;
    hide();
  }

  /* drag & drop */
  dropzone.addEventListener("click", (e) => { if (!e.target.closest("button")) fileInput.click(); });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => { e.preventDefault(); dropzone.classList.remove("drag"); pick(e.dataTransfer.files[0]); });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) pick(fileInput.files[0]); });
  clearBtn.addEventListener("click", (e) => { e.stopPropagation(); reset(); });
  retryBtn.addEventListener("click", reset);

  /* extract */
  extractBtn.addEventListener("click", async () => {
    if (!file) return;
    extractBtn.disabled = true;
    show(stProgress);
    progTitle.textContent = "Uploading PDF...";
    progSub.textContent = "Sending file to extraction service";
    barFill.style.animation = "none";
    void barFill.offsetHeight;
    barFill.style.animation = "";

    const fd = new FormData();
    fd.append("pdf", file);

    try {
      const res = await fetch(ENDPOINT, { method: "POST", body: fd });

      if (!res.ok) {
        let msg = "Server error (" + res.status + ")";
        try { const j = await res.json(); if (j.detail) msg = j.detail; } catch {}
        throw new Error(msg);
      }

      const blob = await res.blob();
      const disp = res.headers.get("Content-Disposition") || "";
      const name = (disp.match(/filename="?([^";\n]+)"?/) || [])[1]
                || file.name.replace(/\.pdf$/i, "") + "_tags_extracted.xlsx";

      const url = URL.createObjectURL(blob);
      dlBtn.href = url;
      dlBtn.download = name;
      doneMsg.textContent = name + " \u2014 " + fmt(blob.size);
      show(stDone);
    } catch (err) {
      errMsg.textContent = err.message || "Unexpected error";
      show(stError);
    } finally {
      extractBtn.disabled = false;
    }
  });
})();
