/** Provides shared connectivity and unsaved-form behavior for the app shell. */
(function () {
  "use strict";

  const offlineBanner = document.querySelector("[data-offline-banner]");
  const guardedForms = document.querySelectorAll("[data-confirm-leave]");
  const dirtyForms = new Set();

  /** Updates global and form-level connection status. */
  function updateConnectionState() {
    if (offlineBanner) {
      offlineBanner.hidden = navigator.onLine;
    }

    if (navigator.onLine) {
      document.querySelectorAll("[data-connection-error]").forEach((error) => {
        error.hidden = true;
      });
    }
  }

  /**
   * Marks a guarded form as changed and exposes its visible save state.
   * @param {Event} event
   */
  function markFormDirty(event) {
    const form = event.currentTarget;
    dirtyForms.add(form);
    const message = form.querySelector("[data-unsaved-message]");
    if (message) {
      message.hidden = false;
    }
  }

  /**
   * Keeps an offline form on the page, or clears its dirty state before a normal POST.
   * @param {SubmitEvent} event
   */
  function handleFormSubmit(event) {
    const form = event.currentTarget;
    if (form.hasAttribute("data-requires-connection") && !navigator.onLine) {
      event.preventDefault();
      const error = form.querySelector("[data-connection-error]");
      if (error) {
        error.hidden = false;
        error.focus();
      }
      return;
    }

    dirtyForms.delete(form);
  }

  /**
   * Requests the browser's standard confirmation when a changed form would be abandoned.
   * @param {BeforeUnloadEvent} event
   */
  function confirmUnsavedChanges(event) {
    if (dirtyForms.size === 0) {
      return;
    }

    event.preventDefault();
    event.returnValue = "";
  }

  guardedForms.forEach((form) => {
    form.addEventListener("input", markFormDirty);
    form.addEventListener("change", markFormDirty);
    form.addEventListener("submit", handleFormSubmit);
  });

  window.addEventListener("online", updateConnectionState);
  window.addEventListener("offline", updateConnectionState);
  window.addEventListener("beforeunload", confirmUnsavedChanges);
  updateConnectionState();
}());
