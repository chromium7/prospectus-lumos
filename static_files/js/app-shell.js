(function () {
  "use strict";

  const offlineBanner = document.querySelector("[data-offline-banner]");

  function updateConnectionState() {
    if (offlineBanner) {
      offlineBanner.hidden = navigator.onLine;
    }
  }

  function initializeChoiceFilters(root) {
    root.querySelectorAll("[data-choice-filter-name][data-choice-filter-group]").forEach(function (controller) {
      const fieldName = controller.dataset.choiceFilterName;
      const groupName = controller.dataset.choiceFilterGroup;
      const inputs = controller.querySelectorAll('[name="' + fieldName + '"]');

      function updateChoices() {
        const selected = controller.querySelector('[name="' + fieldName + '"]:checked');
        if (!selected) {
          return;
        }
        root.querySelectorAll("[data-choice-group]").forEach(function (choice) {
          if (choice.dataset.choiceGroup !== groupName) {
            return;
          }
          const hidden = choice.dataset.choiceValue !== selected.value;
          choice.hidden = hidden;
          if (choice.tagName === "OPTION") {
            choice.disabled = hidden;
          }
        });
        root.querySelectorAll("[data-filtered-choice-group]").forEach(function (wrapper) {
          if (wrapper.dataset.filteredChoiceGroup !== groupName) {
            return;
          }
          const select = wrapper.querySelector("select");
          if (select && select.selectedOptions[0] && select.selectedOptions[0].disabled) {
            select.value = "";
          }
        });
      }

      inputs.forEach(function (input) {
        input.addEventListener("change", updateChoices);
      });
      updateChoices();
    });
  }

  function initializeSelectShortcuts(root) {
    root.querySelectorAll("[data-select-target][data-select-value]").forEach(function (shortcut) {
      shortcut.addEventListener("click", function () {
        const select = document.getElementById(shortcut.dataset.selectTarget);
        if (select) {
          select.value = shortcut.dataset.selectValue;
          select.focus();
        }
      });
    });
  }

  window.AppShell = Object.assign(window.AppShell || {}, {
    initializeChoiceFilters: initializeChoiceFilters,
    initializeSelectShortcuts: initializeSelectShortcuts
  });

  window.addEventListener("online", updateConnectionState);
  window.addEventListener("offline", updateConnectionState);
  updateConnectionState();
  initializeChoiceFilters(document);
  initializeSelectShortcuts(document);
}());
