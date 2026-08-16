// Progressive enhancement for components/file_upload.html.
// Server-side file validation remains required; accept= is only a picker hint.
(() => {
  function initFileUpload(dropzone) {
    if (dropzone.dataset.fileUploadReady === "true") return;

    const input = dropzone.querySelector("[data-file-upload-input]");
    const form = dropzone.closest("form");
    if (!input || !form) return;

    dropzone.dataset.fileUploadReady = "true";
    const prompt = dropzone.querySelector("[data-file-upload-prompt]");
    const loading = dropzone.dataset.loadingId
      ? document.getElementById(dropzone.dataset.loadingId)
      : null;
    const loadingText = dropzone.dataset.loadingTextId
      ? document.getElementById(dropzone.dataset.loadingTextId)
      : null;
    const submitHint = dropzone.dataset.submitHintId
      ? document.getElementById(dropzone.dataset.submitHintId)
      : null;
    const preview = input.dataset.previewId
      ? document.getElementById(input.dataset.previewId)
      : null;
    const previewContainer = input.dataset.previewContainerId
      ? document.getElementById(input.dataset.previewContainerId)
      : null;
    const removeInput = input.dataset.removeInputId
      ? document.getElementById(input.dataset.removeInputId)
      : null;

    function updateSelection() {
      const files = input.files;
      if (!files?.length) return;

      if (prompt) {
        prompt.textContent = files.length === 1 ? files[0].name : `${files.length} files selected`;
      }

      const file = files[0];
      if (preview && file.type.startsWith("image/")) {
        if (preview.dataset.objectUrl) {
          URL.revokeObjectURL(preview.dataset.objectUrl);
        }
        const objectUrl = URL.createObjectURL(file);
        preview.src = objectUrl;
        preview.dataset.objectUrl = objectUrl;
        previewContainer?.classList.remove("hidden");
      } else {
        previewContainer?.classList.add("hidden");
      }

      if (removeInput) removeInput.checked = false;
      if (dropzone.dataset.autoSubmit === "true" && form.reportValidity()) {
        form.requestSubmit();
      }
    }

    function showLoading() {
      const count = input.files?.length || 0;
      if (!count || !loading) return;

      dropzone.classList.add("hidden");
      submitHint?.classList.add("hidden");
      loading.classList.remove("hidden");
      loading.classList.add("flex");

      if (loadingText) {
        const template = count > 1
          ? dropzone.dataset.loadingMultiple
          : dropzone.dataset.loadingSingle;
        loadingText.textContent = template.replace("{count}", count);
      }
    }

    input.addEventListener("change", updateSelection);
    form.addEventListener("submit", showLoading);

    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropzone.classList.add("bg-muted/40", "border-ring");
      });
    });

    ["dragleave", "dragend"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        dropzone.classList.remove("bg-muted/40", "border-ring");
      });
    });

    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropzone.classList.remove("bg-muted/40", "border-ring");

      const droppedFiles = event.dataTransfer?.files;
      if (!droppedFiles?.length) return;
      if (!input.multiple && droppedFiles.length > 1) {
        const singleFile = new DataTransfer();
        singleFile.items.add(droppedFiles[0]);
        input.files = singleFile.files;
      } else {
        input.files = droppedFiles;
      }
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function initFileUploads() {
    document.querySelectorAll("[data-file-upload]").forEach(initFileUpload);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFileUploads);
  } else {
    initFileUploads();
  }
})();
