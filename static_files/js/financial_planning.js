(function () {
    "use strict";

    const form = document.querySelector("[data-planner-form]");
    if (!form) return;

    const eventList = form.querySelector("[data-event-list]");
    const totalForms = form.querySelector("#id_events-TOTAL_FORMS");
    const emptyTemplate = form.querySelector("template[data-empty-event]");
    const presetSelect = form.querySelector("[data-event-preset]");
    const presetsNode = document.getElementById("event-presets-data");
    const presets = presetsNode ? JSON.parse(presetsNode.textContent) : {};
    const loading = form.querySelector("[data-preview-loading]");
    const error = form.querySelector("[data-preview-error]");
    const summary = form.querySelector("[data-preview-summary]");
    let previewTimer = null;
    let previewSequence = 0;
    let previewController = null;

    function activeRows() {
        return Array.from(eventList.querySelectorAll("[data-event-form]")).filter(function (row) {
            const deletion = row.querySelector('input[name$="-DELETE"]');
            return !deletion || !deletion.checked;
        });
    }

    function renumberRows() {
        activeRows().forEach(function (row, index) {
            const number = row.querySelector("[data-event-number]");
            const order = row.querySelector('input[name$="-sort_order"]');
            if (number) number.textContent = String(index + 1);
            if (order) order.value = String(index);
        });
    }

    function addMonths(value, months) {
        if (!value || !months) return "";
        const parts = value.split("-").map(Number);
        const result = new Date(Date.UTC(parts[0], parts[1] - 1 + months, parts[2] || 1));
        return result.toISOString().slice(0, 10);
    }

    function fillPreset(row, preset) {
        if (!preset) return;
        Object.keys(preset).forEach(function (field) {
            const input = row.querySelector('[name$="-' + field + '"]');
            if (input && field !== "label" && field !== "duration_months") input.value = String(preset[field]);
        });
        const eventDate = row.querySelector('input[name$="-event_date"]');
        const recurringEnd = row.querySelector('input[name$="-recurring_end_date"]');
        if (eventDate && !eventDate.value) {
            const nextYear = new Date();
            nextYear.setUTCFullYear(nextYear.getUTCFullYear() + 1);
            nextYear.setUTCDate(1);
            eventDate.value = nextYear.toISOString().slice(0, 10);
        }
        if (recurringEnd && preset.duration_months && eventDate) {
            recurringEnd.value = addMonths(eventDate.value, Number(preset.duration_months) - 1);
        }
    }

    function addEvent() {
        if (!totalForms || !emptyTemplate || Number(totalForms.value) >= 50) return null;
        const index = Number(totalForms.value);
        const wrapper = document.createElement("div");
        wrapper.innerHTML = emptyTemplate.innerHTML.replaceAll("__prefix__", String(index)).replaceAll("__number__", String(index + 1));
        const row = wrapper.firstElementChild;
        eventList.appendChild(row);
        totalForms.value = String(index + 1);
        renumberRows();
        return row;
    }

    function emptyVisibleRow() {
        return activeRows().find(function (row) {
            const id = row.querySelector('input[name$="-id"]');
            const name = row.querySelector('input[name$="-name"]');
            return (!id || !id.value) && name && !name.value;
        });
    }

    function schedulePreview() {
        window.clearTimeout(previewTimer);
        previewTimer = window.setTimeout(requestPreview, 400);
    }

    function formatIdr(value) {
        if (value === null || value === undefined) return "Outside estimate bounds";
        return "Rp" + new Intl.NumberFormat("id-ID", {maximumFractionDigits: 0}).format(Number(value));
    }

    function renderPreview(data) {
        const empty = form.querySelector("[data-summary-empty]");
        if (empty) empty.classList.add("d-none");
        form.querySelector("[data-summary-freedom]").textContent = formatIdr(data.summary.base_freedom_number);
        form.querySelector("[data-summary-target]").textContent = formatIdr(data.summary.total_target);
        form.querySelector("[data-summary-required]").textContent = formatIdr(data.summary.required_monthly_investment);
        form.querySelector("[data-summary-status]").textContent = "Status: " + data.summary.status.replaceAll("_", " ") + ". Server preview.";
        const eventOutflows = data.timeline.filter(function (row) { return Number(row.event_outflow) > 0; });
        const eventSummary = form.querySelector("[data-preview-events]");
        const parts = [];
        if (eventOutflows.length) parts.push(eventOutflows.length + " portfolio event month(s) affect the timeline.");
        if (data.separate_savings.length) parts.push(data.separate_savings.length + " event(s) use separate savings and stay outside the portfolio.");
        eventSummary.textContent = parts.join(" ");
    }

    async function requestPreview() {
        const sequence = ++previewSequence;
        if (previewController) previewController.abort();
        previewController = new AbortController();
        const body = new FormData(form);
        body.set("preview_request_id", String(sequence));
        if (form.dataset.planId) body.set("plan_id", form.dataset.planId);
        loading.classList.remove("d-none");
        error.classList.add("d-none");
        summary.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(form.dataset.previewUrl, {
                method: "POST",
                body: body,
                headers: {"X-Requested-With": "XMLHttpRequest"},
                signal: previewController.signal
            });
            const data = await response.json();
            if (sequence !== previewSequence || data.request_id !== String(sequence)) return;
            if (!response.ok || !data.ok) throw new Error("Invalid preview response");
            renderPreview(data);
        } catch (previewError) {
            if (previewError.name !== "AbortError" && sequence === previewSequence) error.classList.remove("d-none");
        } finally {
            if (sequence === previewSequence) {
                loading.classList.add("d-none");
                summary.setAttribute("aria-busy", "false");
            }
        }
    }

    form.addEventListener("click", function (event) {
        const button = event.target.closest("button");
        if (!button) return;
        if (button.matches("[data-add-event]")) {
            const row = emptyVisibleRow() || addEvent();
            if (row) {
                fillPreset(row, presets[presetSelect.value]);
                row.querySelector("input, select").focus();
                schedulePreview();
            }
        } else if (button.matches("[data-event-remove]")) {
            const row = button.closest("[data-event-form]");
            const deletion = row.querySelector('input[name$="-DELETE"]');
            if (deletion) deletion.checked = true;
            row.classList.add("d-none");
            renumberRows();
            schedulePreview();
        } else if (button.matches("[data-event-up], [data-event-down]")) {
            const row = button.closest("[data-event-form]");
            const sibling = button.matches("[data-event-up]") ? row.previousElementSibling : row.nextElementSibling;
            if (sibling) {
                if (button.matches("[data-event-up]")) eventList.insertBefore(row, sibling);
                else eventList.insertBefore(sibling, row);
                renumberRows();
                schedulePreview();
            }
        }
    });
    form.addEventListener("input", schedulePreview);
    form.addEventListener("change", schedulePreview);
    renumberRows();
}());
