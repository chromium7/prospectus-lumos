(function () {
  "use strict";

  const root = document.querySelector("[data-dashboard]");
  if (!root) return;

  const loading = root.querySelector("[data-dashboard-loading]");
  const error = root.querySelector("[data-dashboard-error]");
  const empty = root.querySelector("[data-dashboard-empty]");
  const content = root.querySelector("[data-dashboard-content]");
  const money = new Intl.NumberFormat("id-ID", {
    style: "currency", currency: "IDR", maximumFractionDigits: 0,
  });

  function amount(value) {
    return money.format(Number(value || 0)).replace(/\u00a0/g, " ");
  }

  function text(selector, value) {
    const element = root.querySelector(selector);
    if (element) element.textContent = value;
  }

  function show(name) {
    loading.hidden = name !== "loading";
    error.hidden = name !== "error";
    empty.hidden = name !== "empty";
    content.hidden = name !== "content";
    loading.setAttribute("aria-busy", name === "loading" ? "true" : "false");
  }

  function renderBudget(budget) {
    const target = root.querySelector("[data-dashboard-budget]");
    target.replaceChildren();
    const categories = (budget && budget.categories) || [];
    if (!categories.length) {
      const note = document.createElement("p");
      note.className = "text-muted mb-0";
      note.textContent = "No budget is set for this month yet.";
      target.append(note);
      return;
    }
    categories.slice(0, 3).forEach(function (category) {
      const percent = Math.max(0, Math.min(Number(category.percentage_used || 0), 100));
      const row = document.createElement("div");
      row.className = "budget-progress-row";
      row.innerHTML = "<div><strong></strong><span></span></div><div class=\"progress\" role=\"progressbar\" aria-valuemin=\"0\" aria-valuemax=\"100\"><div class=\"progress-bar\"></div></div>";
      row.querySelector("strong").textContent = category.name;
      row.querySelector("span").textContent = category.remaining < 0
        ? amount(Math.abs(category.remaining)) + " over"
        : amount(category.remaining) + " left";
      const progress = row.querySelector(".progress");
      progress.setAttribute("aria-label", category.name + ": " + Math.round(percent) + "% used");
      progress.setAttribute("aria-valuenow", String(Math.round(percent)));
      progress.querySelector(".progress-bar").style.width = percent + "%";
      target.append(row);
    });
  }

  function renderActivity(items) {
    const target = root.querySelector("[data-dashboard-activity]");
    target.replaceChildren();
    if (!items.length) {
      const item = document.createElement("li");
      item.className = "text-muted";
      item.textContent = "No transactions in this month yet.";
      target.append(item);
      return;
    }
    items.slice(0, 5).forEach(function (transaction) {
      const item = document.createElement("li");
      const sign = transaction.type === "expense" ? "−" : transaction.type === "income" ? "+" : "";
      item.innerHTML = "<span class=\"dashboard-activity__icon\" aria-hidden=\"true\"><i class=\"bi bi-arrow-left-right\"></i></span><span class=\"dashboard-activity__label\"><strong></strong><small></small></span><strong class=\"dashboard-activity__amount\"></strong>";
      item.querySelector(".dashboard-activity__label strong").textContent = transaction.payee || transaction.category || "Transfer";
      item.querySelector("small").textContent = transaction.occurred_on || "";
      item.querySelector(".dashboard-activity__amount").textContent = sign + amount(transaction.amount);
      target.append(item);
    });
  }

  function render(data) {
    const accounts = data.account_balances || [];
    if (!accounts.length) {
      show("empty");
      return;
    }
    const totals = data.totals || {};
    const remaining = totals.remaining === undefined ? totals.net : totals.remaining;
    text("[data-dashboard-income]", amount(totals.income));
    text("[data-dashboard-expense]", amount(totals.expense));
    text("[data-dashboard-net]", amount(remaining));
    text("[data-dashboard-summary]", remaining < 0
      ? "You spent " + amount(Math.abs(remaining)) + " more than came in this month."
      : "You have " + amount(remaining) + " left this month.");
    renderBudget(data.budget_progress || data.budget);
    renderActivity(data.recent_activity || []);
    show("content");
  }

  function load() {
    show("loading");
    const url = new URL(root.dataset.apiUrl, window.location.origin);
    url.searchParams.set("month", root.dataset.month);
    fetch(url, {headers: {Accept: "application/json"}, credentials: "same-origin"})
      .then(function (response) {
        if (!response.ok) throw new Error("The dashboard service returned " + response.status + ".");
        return response.json();
      })
      .then(render)
      .catch(function (requestError) {
        text("[data-dashboard-error-message]", navigator.onLine
          ? "Your information is safe. " + requestError.message + " Try again."
          : "You are offline. Reconnect to refresh this month.");
        show("error");
        error.focus();
      });
  }

  root.querySelector("[data-dashboard-retry]").addEventListener("click", load);
  load();
}());
