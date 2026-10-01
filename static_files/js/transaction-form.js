(function () {
  "use strict";

  const form = document.querySelector("[data-transaction-form] form");
  if (!form) {
    return;
  }

  const categoryField = form.querySelector("[data-category-field]");
  const transferField = form.querySelector("[data-transfer-field]");
  const accountLabel = form.querySelector("[data-account-label]");
  const categorySelect = form.querySelector("[name=\"category\"]");

  function updateTypeFields() {
    const checked = form.querySelector('input[name="type"]:checked');
    const isTransfer = checked && checked.value === "transfer";
    categoryField.hidden = isTransfer;
    transferField.hidden = !isTransfer;
    accountLabel.textContent = isTransfer ? "From account" : "Account";
    if (!isTransfer && categorySelect) {
      categorySelect.querySelectorAll("option[data-category-type]").forEach(function (option) {
        option.hidden = option.dataset.categoryType !== checked.value;
        option.disabled = option.hidden;
      });
      if (categorySelect.selectedOptions[0] && categorySelect.selectedOptions[0].disabled) {
        categorySelect.value = "";
      }
    }
    form.querySelectorAll("[data-shortcut-category-type]").forEach(function (shortcut) {
      shortcut.hidden = isTransfer || shortcut.dataset.shortcutCategoryType !== checked.value;
    });
  }

  form.querySelectorAll('input[name="type"]').forEach(function (input) {
    input.addEventListener("change", updateTypeFields);
  });
  form.querySelectorAll("[data-select-target]").forEach(function (shortcut) {
    shortcut.addEventListener("click", function () {
      const select = document.getElementById(shortcut.dataset.selectTarget);
      if (select) {
        select.value = shortcut.dataset.selectValue;
        select.focus();
      }
    });
  });
  updateTypeFields();
}());
