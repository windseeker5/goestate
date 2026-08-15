// Project-specific JavaScript.
//
// Rule of thumb (see AGENTS.md): if Flask/Jinja can do it server-side, do it
// there. Only reach for JS for genuine browser-only interactions that
// Basecoat's vendor bundle (basecoat.all.min.js) does not already cover.

(() => {
  // Documents page: dropzone (click or drag-and-drop) + upload loading state.
  //
  // The <label for="upload-file-input"> already makes click-to-browse work
  // with zero JS (native HTML behavior). Everything here is purely for:
  //   1. Drag-and-drop support (native <input type="file"> doesn't get this
  //      for free — the browser needs explicit dragover/drop handling).
  //   2. Auto-submitting the form the moment files are chosen, so there's
  //      no separate "upload" button to click after picking files.
  //   3. Swapping the dropzone for a loading spinner on submit, since
  //      ingestion runs synchronously server-side (see routes/documents.py)
  //      and a multi-file upload can take a while — without this the page
  //      looks frozen with no feedback.
  function initDocumentsDropzone() {
    const dropzone = document.getElementById("upload-dropzone");
    const fileInput = document.getElementById("upload-file-input");
    const form = document.getElementById("upload-form");
    const loading = document.getElementById("upload-loading");
    const loadingText = document.getElementById("upload-loading-text");
    const hint = document.getElementById("upload-hint");

    if (!dropzone || !fileInput || !form) return;

    const showLoading = (fileCount) => {
      dropzone.classList.add("hidden");
      if (hint) hint.classList.add("hidden");
      if (loading) {
        loading.classList.remove("hidden");
        loading.classList.add("flex");
      }
      if (loadingText) {
        loadingText.textContent =
          fileCount > 1
            ? `Processing ${fileCount} documents…`
            : "Processing your document…";
      }
    };

    // Auto-submit as soon as files are selected via the native picker.
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files.length > 0) {
        showLoading(fileInput.files.length);
        form.submit();
      }
    });

    // Drag-and-drop: highlight on dragover, assign dropped files to the
    // input (so the same "change" handling / form submission path is
    // reused), and submit.
    ["dragenter", "dragover"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add("bg-muted/40", "border-ring");
      });
    });

    ["dragleave", "dragend"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove("bg-muted/40", "border-ring");
      });
    });

    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("bg-muted/40", "border-ring");

      const files = e.dataTransfer?.files;
      if (!files || files.length === 0) return;

      fileInput.files = files;
      showLoading(files.length);
      form.submit();
    });

    // Also show the loading state if the form is submitted through any
    // other path (defensive — keeps behavior consistent).
    form.addEventListener("submit", () => {
      if (fileInput.files && fileInput.files.length > 0) {
        showLoading(fileInput.files.length);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDocumentsDropzone);
  } else {
    initDocumentsDropzone();
  }
})();
