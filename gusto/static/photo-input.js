(function () {
  "use strict";

  document.querySelectorAll("[data-photo-picker]").forEach(function (picker) {
    const inputs = Array.from(picker.querySelectorAll("[data-photo-input]"));
    const preview = picker.querySelector("[data-photo-preview]");
    const previewImage = picker.querySelector("[data-photo-preview-image]");
    const previewName = picker.querySelector("[data-photo-preview-name]");
    const previewSource = picker.querySelector("[data-photo-preview-source]");
    const clear = picker.querySelector("[data-photo-clear]");
    let previewUrl = null;

    function resetPreview() {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      preview.hidden = true;
      previewImage.removeAttribute("src");
    }

    inputs.forEach(function (input) {
      input.addEventListener("change", function () {
        const file = input.files && input.files[0];
        if (!file) {
          resetPreview();
          return;
        }
        inputs.forEach(function (other) {
          if (other !== input) other.value = "";
        });
        resetPreview();
        previewUrl = URL.createObjectURL(file);
        previewImage.src = previewUrl;
        previewName.textContent = file.name || "Neues Foto";
        previewSource.textContent = input.dataset.photoSource + " · wird optimiert";
        preview.hidden = false;
      });
    });

    clear.addEventListener("click", function () {
      inputs.forEach(function (input) { input.value = ""; });
      resetPreview();
    });
  });
})();
