/**
 * Tests for the planner's local-calendar date helpers.
 *
 * Run under `node --test`. The Python suite drives this file once per timezone so the helpers are
 * checked east and west of UTC; see tests/test_financial_planning_js.py.
 */
const test = require("node:test");
const assert = require("node:assert");

const {addMonths, today, toISODate} = require("../../static_files/js/financial_planning_dates.js");

test("addMonths keeps the day of the month when the target month is long enough", () => {
    assert.strictEqual(addMonths("2026-09-19", 12), "2027-09-19");
    assert.strictEqual(addMonths("2026-09-19", 0), "2026-09-19");
    assert.strictEqual(addMonths("2026-09-19", 1), "2026-10-19");
    assert.strictEqual(addMonths("2026-01-15", 23), "2027-12-15");
});

test("addMonths does not drift a day across timezones", () => {
    // The old implementation round-tripped through toISOString(), which serialises UTC: east of
    // UTC every result lost a day, west of UTC it gained one.
    assert.strictEqual(addMonths("2026-01-01", 1), "2026-02-01");
    assert.strictEqual(addMonths("2026-12-31", 1), "2027-01-31");
    assert.strictEqual(addMonths("2026-06-30", 6), "2026-12-30");
});

test("addMonths clamps to the last day of a shorter target month", () => {
    assert.strictEqual(addMonths("2026-01-31", 1), "2026-02-28");
    assert.strictEqual(addMonths("2028-01-31", 1), "2028-02-29");
    assert.strictEqual(addMonths("2026-01-30", 1), "2026-02-28");
    assert.strictEqual(addMonths("2026-01-29", 1), "2026-02-28");
    assert.strictEqual(addMonths("2026-03-31", 1), "2026-04-30");
    assert.strictEqual(addMonths("2026-05-31", 13), "2027-06-30");
});

test("addMonths handles negative shifts and year boundaries", () => {
    assert.strictEqual(addMonths("2026-03-31", -1), "2026-02-28");
    assert.strictEqual(addMonths("2026-01-15", -1), "2025-12-15");
    assert.strictEqual(addMonths("2026-01-15", -13), "2024-12-15");
});

test("toISODate reads the local calendar fields, not the UTC ones", () => {
    // Local midnight on 1 January is the previous year in UTC east of the line, and local 23:00
    // on 31 December is the next year in UTC west of it. Both must still read as their local day.
    assert.strictEqual(toISODate(new Date(2026, 0, 1, 0, 0, 0)), "2026-01-01");
    assert.strictEqual(toISODate(new Date(2026, 11, 31, 23, 0, 0)), "2026-12-31");
    assert.strictEqual(toISODate(new Date(2026, 8, 9, 12, 0, 0)), "2026-09-09");
});

test("today returns the user's calendar day", () => {
    const now = new Date();
    assert.strictEqual(today(), toISODate(now));
    assert.match(today(), /^\d{4}-\d{2}-\d{2}$/);
});
