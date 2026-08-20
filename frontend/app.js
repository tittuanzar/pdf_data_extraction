(() => {
  const MODES = {
    tagwise: {
      endpoint: "/api/extract-Tag-wise-Enquiry-pdf",
      kind: "file",
      heroSub: "Upload a Tag-wise Enquiry PDF. Get structured Excel output automatically.",
      hint: "",
      actionLabel: "Extract to Excel",
      fallbackSuffix: "_tags_extracted.xlsx",
      doneTitle: "Extraction Complete",
    },
    generic: {
      endpoint: "/api/extract-configured-pdf",
      kind: "file",
      heroSub: "Upload a document PDF. Extraction runs against the last uploaded configuration.",
      hint: "Uses the configuration most recently uploaded via “Upload Extraction Configuration” below.",
      actionLabel: "Extract to Excel",
      fallbackSuffix: "_extracted.xlsx",
      doneTitle: "Extraction Complete",
    },
    config: {
      endpoint: "/api/upload-extraction-configuration",
      kind: "json",
      heroSub: "Upload a configuration PDF defining the fields to extract for Generic Document processing.",
      hint: "This replaces any previously uploaded configuration.",
      actionLabel: "Upload Configuration",
      doneTitle: "Configuration Uploaded",
    },
  };

  const $ = (s) => document.querySelector(s);

  const docType        = $("#doc-type");
  const heroSub        = $("#hero-sub");
  const modeHint        = $("#mode-hint");
  const extractBtn      = $("#extract-btn");
  const extractBtnLabel = $("#extract-btn-label");

  const dropzone   = $("#dropzone");
  const dzEmpty    = $("#dropzone-empty");
  const dzFile     = $("#dropzone-file");
  const fileInput  = $("#file-input");
  const fileName   = $("#file-name");
  const fileMeta   = $("#file-meta");
  const clearBtn   = $("#clear-btn");

  const stProgress = $("#state-progress");
  const stError    = $("#state-error");
  const stDone     = $("#state-done");

  const progTitle = $("#prog-title");
  const progSub   = $("#prog-sub");
  const barFill   = $("#bar-fill");
  const errMsg    = $("#err-msg");
  const retryBtn  = $("#retry-btn");
  const doneTitle = $("#done-title");
  const doneMsg   = $("#done-msg");
  const dlBtn     = $("#download-btn");
  const resetBtn  = $("#reset-btn");

  let file = null;

  /* helpers */
  const fmt = (b) => b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB";
  const hide = () => { stProgress.classList.add("hidden"); stError.classList.add("hidden"); stDone.classList.add("hidden"); };
  const show = (el) => { hide(); el.classList.remove("hidden"); };

  function currentMode() { return MODES[docType.value]; }

  function applyMode() {
    const mode = currentMode();
    heroSub.textContent = mode.heroSub;
    extractBtnLabel.textContent = mode.actionLabel;
    if (mode.hint) {
      modeHint.textContent = mode.hint;
      modeHint.classList.remove("hidden");
    } else {
      modeHint.classList.add("hidden");
    }
    hide();
  }

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
  resetBtn.addEventListener("click", reset);

  docType.addEventListener("change", applyMode);
  applyMode();

  /* extract / upload */
  extractBtn.addEventListener("click", async () => {
    if (!file) return;
    const mode = currentMode();

    extractBtn.disabled = true;
    show(stProgress);
    progTitle.textContent = mode.kind === "json" ? "Processing configuration..." : "Processing PDF...";
    progSub.textContent = "Sending file to extraction service";
    barFill.style.animation = "none";
    void barFill.offsetHeight;
    barFill.style.animation = "";

    const fd = new FormData();
    fd.append("pdf", file);

    try {
      const res = await fetch(mode.endpoint, { method: "POST", body: fd });

      if (!res.ok) {
        let msg = "Server error (" + res.status + ")";
        try { const j = await res.json(); if (j.detail) msg = j.detail; } catch {}
        throw new Error(msg);
      }

      doneTitle.textContent = mode.doneTitle;

      if (mode.kind === "json") {
        const data = await res.json().catch(() => ({}));
        const categories = Array.isArray(data.main_categories) ? data.main_categories : [];
        doneMsg.textContent = (data.message || "Configuration uploaded.")
          + (categories.length ? " Categories: " + categories.join(", ") : "");
        dlBtn.classList.add("hidden");
      } else {
        const blob = await res.blob();
        const disp = res.headers.get("Content-Disposition") || "";
        const name = (disp.match(/filename="?([^";\n]+)"?/) || [])[1]
                  || file.name.replace(/\.pdf$/i, "") + mode.fallbackSuffix;

        const url = URL.createObjectURL(blob);
        dlBtn.href = url;
        dlBtn.download = name;
        dlBtn.classList.remove("hidden");
        doneMsg.textContent = name + " — " + fmt(blob.size);
      }

      show(stDone);
    } catch (err) {
      errMsg.textContent = err.message || "Unexpected error";
      show(stError);
    } finally {
      extractBtn.disabled = false;
    }
  });
})();
