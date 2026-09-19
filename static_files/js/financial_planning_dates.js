/**
 * Local-calendar date helpers for the planner wizard.
 *
 * Every date the planner shows or submits is a plain calendar day in the user's own timezone.
 * `Date.prototype.toISOString()` serialises UTC, so using it to read a date back out shifts the
 * day for anyone east or west of UTC (in Asia/Jakarta, UTC+7, local midnight is the previous day
 * in UTC). These helpers stay in local time from end to end.
 */
(function (root, factory) {
    "use strict";
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.PlannerDates = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function pad(value) {
        return String(value).padStart(2, "0");
    }

    /** Format a Date as YYYY-MM-DD using its local calendar fields. */
    function toISODate(date) {
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    /** Today's date in the user's timezone, as YYYY-MM-DD. */
    function today() {
        return toISODate(new Date());
    }

    /** Number of days in a given month; day 0 of the next month is the last day of this one. */
    function daysInMonth(year, monthIndex) {
        return new Date(year, monthIndex + 1, 0).getDate();
    }

    /**
     * Shift a YYYY-MM-DD date by whole months, clamping to the end of the target month.
     *
     * `setMonth()` overflows instead of clamping: 31 January plus one month gives 2 or 3 March.
     * Clamping keeps the month the user asked for, so 31 January plus one month is 28 February.
     */
    function addMonths(value, months) {
        const [year, month, day] = String(value).split("-").map(Number);
        const shifted = new Date(year, month - 1 + months, 1);
        const clampedDay = Math.min(day, daysInMonth(shifted.getFullYear(), shifted.getMonth()));
        return `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(clampedDay)}`;
    }

    return {addMonths, today, toISODate};
}));
