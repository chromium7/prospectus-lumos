(function () {
  "use strict";

  const offlineBanner = document.querySelector("[data-offline-banner]");

  function updateConnectionState() {
    if (offlineBanner) {
      offlineBanner.hidden = navigator.onLine;
    }
  }

  window.addEventListener("online", updateConnectionState);
  window.addEventListener("offline", updateConnectionState);
  updateConnectionState();
}());
