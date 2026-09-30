(function () {
  "use strict";

  var root = document.documentElement;
  var themeButtons = document.querySelectorAll("[data-theme-toggle]");
  var offlineBanner = document.querySelector("[data-offline-banner]");

  function preferredTheme() {
    if (root.dataset.theme === "light" || root.dataset.theme === "dark") {
      return root.dataset.theme;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function updateThemeButtons() {
    var isDark = preferredTheme() === "dark";
    themeButtons.forEach(function (button) {
      button.setAttribute("aria-pressed", String(isDark));
      button.setAttribute("aria-label", isDark ? "Use light theme" : "Use dark theme");
    });
  }

  themeButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var nextTheme = preferredTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = nextTheme;
      localStorage.setItem("lumos-theme", nextTheme);
      updateThemeButtons();
    });
  });

  function updateConnectionState() {
    if (!offlineBanner) {
      return;
    }
    offlineBanner.hidden = navigator.onLine;
  }

  window.addEventListener("online", updateConnectionState);
  window.addEventListener("offline", updateConnectionState);
  updateThemeButtons();
  updateConnectionState();
}());
