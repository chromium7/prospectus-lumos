(function () {
    "use strict";

    const form = document.querySelector("[data-event-formset]");
    if (!form) return;

    const list = form.querySelector("[data-event-list]");
    const template = form.querySelector("[data-event-template]");
    const totalInput = form.querySelector("[name='events-TOTAL_FORMS']");
    const presetsElement = document.getElementById("event-presets");
    const presets = presetsElement ? JSON.parse(presetsElement.textContent) : {};
    const maxEvents = Number(form.dataset.maxEvents || 50);

    function visibleRows() {
        return Array.from(list.querySelectorAll("[data-event-form]")).filter((row) => !row.hidden);
    }

    function renumberRows() {
        visibleRows().forEach((row, index) => {
            const number = row.querySelector("[data-event-number]");
            const sortOrder = row.querySelector("[name$='-sort_order']");
            if (number) number.textContent = String(index + 1);
            if (sortOrder) sortOrder.value = String(index);
        });
    }

    const {addMonths, today} = window.PlannerDates;

    function addRow(preset) {
        if (visibleRows().length >= maxEvents) return null;
        const index = Number(totalInput.value);
        const fragment = document.createElement("div");
        fragment.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index));
        const row = fragment.firstElementChild;
        list.appendChild(row);
        totalInput.value = String(index + 1);
        if (preset) {
            const eventDate = addMonths(today(), 12);
            Object.entries(preset).forEach(([name, value]) => {
                const field = row.querySelector(`[name$='-${name}']`);
                if (field && name !== "duration_months" && name !== "label") field.value = String(value);
            });
            row.querySelector("[name$='-event_date']").value = eventDate;
            if (Number(preset.duration_months) > 0) {
                row.querySelector("[name$='-recurring_end_date']").value = addMonths(
                    eventDate,
                    Number(preset.duration_months) - 1,
                );
            }
        }
        renumberRows();
        row.querySelector("input:not([type='hidden']), select")?.focus();
        return row;
    }

    form.addEventListener("click", (event) => {
        const target = event.target.closest("button");
        if (!target) return;
        if (target.matches("[data-event-add]")) addRow(null);
        if (target.matches("[data-event-preset]")) addRow(presets[target.dataset.eventPreset]);
        const row = target.closest("[data-event-form]");
        if (!row) return;
        if (target.matches("[data-event-remove]")) {
            const deleteInput = row.querySelector("[name$='-DELETE']");
            if (deleteInput) deleteInput.checked = true;
            row.hidden = true;
            renumberRows();
        }
        if (target.matches("[data-event-up], [data-event-down]")) {
            const rows = visibleRows();
            const index = rows.indexOf(row);
            const sibling = target.matches("[data-event-up]") ? rows[index - 1] : rows[index + 1];
            if (sibling) {
                if (target.matches("[data-event-up]")) list.insertBefore(row, sibling);
                else list.insertBefore(sibling, row);
                renumberRows();
            }
        }
    });

    renumberRows();
}());
