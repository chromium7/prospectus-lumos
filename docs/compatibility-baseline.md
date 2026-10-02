# Direct-entry and simple-budgeting compatibility baseline

This baseline describes behavior on `origin/master` at `7b31283` before the
revised direct-entry and simple-budgeting work. It is intentionally limited to
contracts that later slices must preserve. It does not adopt the earlier ledger,
workspace, or budgeting API designs.

## Supported verification command

The supported local and CI test command is:

```shell
python manage.py test
```

`.github/workflows/tests.yml` runs that command on Python 3.13 with PostgreSQL
15 and Redis after `python manage.py check`. Targeted compatibility checks are:

```shell
python manage.py test tests.test_expenses.ExpenseSheetServiceSyncTests
python manage.py test tests.test_financial_planning_services.FreedomScenarioServiceTests
python manage.py test tests.test_financial_planning_calculator.FinancialFreedomCalculatorTests
```

The top-level `tests/` package is the canonical test location. There is no
pytest configuration.

## Existing monthly-data contract

The current storage path is `DocumentSource -> Document -> Transaction`:

- A `DocumentSource` belongs to one Django user. Its persisted source types are
  `google_drive` and `direct_upload`; its name is unique only within that user.
- A `Document` belongs to one user and one source and represents one numeric
  `(year, month)`. The database permits only one document per user-month,
  regardless of source. `total_income`, `total_expenses`, and both counts are
  denormalized values stored independently from transaction rows.
- A `Transaction` must belong to a document. Its type is `income` or `expense`,
  amount is a two-decimal `Decimal`, and description/category are plain text.
  There is no account, workspace, transfer, pending, soft-delete, or separate
  origin field on `master`.

Representative import values are executable in
`tests.test_expenses.ExpenseSheetServiceSyncTests`:

| Period | Income | Expenses | Raw dates | Categories |
| --- | ---: | ---: | --- | --- |
| 2025-08 initial import | 500000.00 | 15000.00 | `1/8/2025`, `2/8/2025` | `Food`, `Paycheck` |
| 2025-08 replacement | 750000.25 | 25000.50 | `31/8/2025`, `1/8/2025` | `Food & Dining`, `Primary Income` |

Date and category strings are preserved exactly as parsed. `Transaction.date`
is a `CharField`, so its default ordering is lexicographic rather than calendar
ordering. Categories are case-sensitive strings and may be blank. Code must not
assume normalized dates or category records without an explicit migration.

The generated CSV has the exact header
`name,amount,description,category,expense/income`. It does not contain the raw
date and currently repeats the description in both `name` and `description`.

## Import replacement behavior

`ExpenseSheetService.sync_google_drive_documents` resolves an existing row by
user, month, and year:

- The same Google Sheet ID is a no-op: the sheet is not parsed, the existing
  document is not returned, and its rows remain unchanged.
- A different Sheet ID for the same user-month reuses the existing `Document`
  primary key, deletes every old transaction, updates sheet metadata, totals,
  counts, and CSV, then creates the parsed rows again.
- The document's existing `source` is retained during replacement, even when
  sync was invoked with another source.
- A new user-month creates a new document and transactions.
- Each completed sync updates `DocumentSource.last_sync`, including a sync that
  only skipped unchanged documents.

The replacement sequence is not currently wrapped in one database transaction.
Later work should preserve the visible result above and may make it atomic, but
must not silently append duplicates or change the same-ID no-op behavior.

## Planner compatibility fixtures

The planner consumes document header totals, not a ledger or account balance.
`ActualsSnapshotService` uses only complete months owned by the requesting user,
does not treat missing months as zero, and applies category exclusions by
subtracting matching transaction totals from the document headers (clamped at
zero when rows and headers disagree).

The representative tracked-actuals fixture in
`FreedomScenarioServiceTests.test_actuals_snapshot_uses_represented_months_exclusions_and_owned_data`
is the compatibility anchor:

- Snapshot date: 2026-09-06; requested period: three months.
- Represented complete months: 2026-03 and 2026-04; missing month: 2026-02.
- Exclusions: income `Bonus`, expense `Rent`.
- Result: two represented months, average income `95.00`, average expenses
  `20.00`, and average net savings `75.00`.
- An unrelated user's document and the current partial month are excluded.

Calculator fixtures in `tests/test_financial_planning_calculator.py` separately
lock the pure planning math. Notable anchors are a `6000000000` base freedom
number for a `20000000` monthly lifestyle at a 4% withdrawal rate, and an ending
balance of `1020000000` after two zero-return, end-of-month contributions of
`10000000` to `1000000000` of starting assets.

## Earlier ledger and API work

The following work is not part of the `master` compatibility surface:

- `feat/APE-149-ledger-models` adds accounts, normalized categories, direct
  ledger transactions, transfers, archive/delete semantics, budget periods,
  allocations, and a large service layer. It is unmerged.
- `feat/APE-149-ledger-api` currently points at `master`; it contributes no
  ledger API implementation.
- The APE-153 direct-entry branches are stacked on the unmerged ledger models,
  so their account/category dependencies are not prerequisites for the revised
  flow.
- Earlier branches `feat/54-request-id-error-envelope`,
  `feat/55-openapi-schema`, `feat/56-contract-fixtures`, and the 57-59 tenancy
  series contain useful experiments but are not ancestors of current `master`.
  In particular, their workspace/membership contracts are not required here.
- Current `master` exposes only `/api/ping` under the DRF API. It has no document,
  transaction, budget, or planner API contract. The server-rendered `/app/`
  dashboard validates a `YYYY-MM` selector but currently passes
  `dashboard=None` rather than loading ledger data.

Later slices must therefore use current models and server-rendered behavior as
their baseline, not import or recreate those branch-only domains.

## Minimal contracts for later slices

These are compatibility boundaries, not new models introduced by this slice:

- **Document:** preserve one user-owned monthly aggregate with its existing
  totals, counts, CSV, sheet identity, and transaction relationship. Existing
  imports and planner snapshots must remain readable.
- **Origin:** retain enough lineage to distinguish imported data from direct
  entry. The existing document source is sufficient for imported months; a
  direct-entry implementation may add a small explicit origin marker, but must
  not require financial accounts, workspaces, or a replacement ledger.
- **Budget:** the simple persisted shape needs only an owner, calendar month,
  expense-category label, and non-negative planned amount. Actual spending can
  be derived from the existing user-owned expense transactions. Accounts,
  transfers, rollover, workspace membership, and a public API are outside this
  compatibility contract.

Any later schema change should prove the representative totals, raw formats,
replacement semantics, and planner fixtures above still pass through the
supported Django test command.
