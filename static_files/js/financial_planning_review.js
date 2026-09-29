(function () {
    "use strict";

    const form = document.querySelector("[data-review-form]");
    if (!form) return;

    let previewTimer = null;
    let previewController = null;
    let previewSequence = 0;
    const state = form.querySelector("[data-preview-state]");
    const error = document.querySelector("[data-preview-error]");

    // Money arrives pre-formatted from the server so the browser never re-derives or re-rounds it;
    // re-formatting here would disagree with the server-rendered `|idr` values on the same screen,
    // and Number() would silently lose precision on targets above Number.MAX_SAFE_INTEGER.
    function rupiah(text) {
        if (text === null || text === undefined || text === "") return "Outside estimate range";
        return text;
    }

    function setText(selector, value) {
        document.querySelectorAll(selector).forEach((element) => {
            element.textContent = value;
        });
    }

    function updateTimeline(timeline) {
        const body = document.querySelector("[data-preview-timeline]");
        if (!body) return;
        const checkpoints = timeline.filter((row, index) => index % 60 === 0 || index === timeline.length - 1);
        body.replaceChildren(...checkpoints.map((row) => {
            const tr = document.createElement("tr");
            const year = document.createElement("td");
            const balance = document.createElement("td");
            const target = document.createElement("td");
            year.textContent = row.date.slice(0, 4);
            balance.className = "text-end";
            balance.textContent = rupiah(row.closing_balance_text);
            target.className = "text-end";
            target.textContent = rupiah(row.target_text);
            tr.append(year, balance, target);
            return tr;
        }));
    }

    function updateSeparate(items) {
        const container = document.querySelector("[data-preview-separate]");
        if (!container) return;
        if (!items.length) {
            container.innerHTML = '<p class="text-muted mb-0">You did not mark any future plans for separate savings.</p>';
            return;
        }
        container.replaceChildren(...items.map((item) => {
            const line = document.createElement("p");
            line.className = "mb-2";
            line.textContent = `${item.name}: save about ${rupiah(item.monthly_funding_need_text)} a month separately by ${item.event_date}.`;
            return line;
        }));
    }

    async function refreshPreview() {
        const sequence = ++previewSequence;
        if (previewController) previewController.abort();
        previewController = new AbortController();
        const data = new FormData(form);
        data.set("plan_id", form.dataset.planId);
        data.set("preview_request_id", String(sequence));
        state.textContent = "Updating estimate…";
        error.classList.add("d-none");
        try {
            const response = await fetch(form.dataset.previewUrl, {
                method: "POST",
                body: data,
                signal: previewController.signal,
                headers: {"X-Requested-With": "XMLHttpRequest"},
            });
            const result = await response.json();
            if (sequence !== previewSequence || result.request_id !== String(sequence)) return;
            if (!response.ok || !result.ok) throw new Error("Preview validation failed");
            setText("[data-preview-required]", rupiah(result.summary.required_monthly_investment_text));
            setText("[data-preview-target]", rupiah(result.summary.total_target_text));
            setText("[data-preview-date]", result.summary.projected_achievement_date || "Not reached in this estimate");
            setText("[data-preview-progress]", `${result.summary.progress_percent}%`);
            updateTimeline(result.timeline);
            updateSeparate(result.separate_savings);
            state.textContent = "Preview updated. Apply to keep these assumptions.";
        } catch (previewError) {
            if (previewError.name === "AbortError") return;
            error.classList.remove("d-none");
            state.textContent = "Last saved estimate shown.";
        }
    }

    function schedulePreview() {
        window.clearTimeout(previewTimer);
        previewTimer = window.setTimeout(refreshPreview, 400);
    }

    form.addEventListener("input", schedulePreview);
    form.addEventListener("change", schedulePreview);
}());
