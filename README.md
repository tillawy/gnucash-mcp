# gnucash-mcp

<!-- mcp-name: io.github.tillawy/gnucash-mcp -->

An MCP server that lets Claude read your GnuCash book and import bank statements into it.
Upload a statement CSV from any bank, and Claude works out its layout, proposes a
category for each row, and shows a preview. It flags duplicates before writing
anything.

It has two parts:

- `gnucash_storage`: a piecash wrapper, configured per book, with read helpers and
  idempotent writes.
- `gnucash_mcp`: the MCP server that exposes the tools and prompts over stdio.

## Requirements: a SQLite book

GnuCash saves books as XML by default, usually gzip-compressed. piecash, which this
project is built on, can't read XML. It only reads GnuCash's SQL formats, and this server
opens SQLite files. To convert your book:

1. Open it in GnuCash.
2. Choose **File > Save As**.
3. Set **Data Format** to **sqlite3**, pick a file name, and save.
4. Point `book_path` in your book TOML at the new file.

Keep using the SQLite file in GnuCash from then on. The XML file won't see the imported
transactions. If `book_path` points at an XML book, every tool fails with a message
explaining this conversion.

You also need [uv](https://docs.astral.sh/uv/) and Python 3.13 or newer. uv installs
Python for you if needed.

## Configure a book

Create a TOML file for your book, for example `~/gnucash/personal.toml`:

```toml
book_path = "personal.gnucash"   # relative to this TOML file, or an absolute path
bank_account = "Assets:Current Assets:Checking Account"  # default bank side for imports
open_if_lock = false             # open for writing even while GnuCash has the file open
do_backup = true                 # piecash writes a timestamped backup before writing

# Optional: "posting" (booking date) or "transaction" (when the purchase happened).
date_source = "posting"

# Optional hints about your bank's statement format, added to the import prompts.
statement_notes = '''
Details holds the merchant and a card reference "REF <12 digits>" (use it as
external_id). The card date inside Details is YY/MM/DD.
'''
```

Only `book_path` is required.

`statement_notes` is the place for your bank's quirks: where a reference ID hides, which
of two date columns to use, or what an odd column means. Claude reads the notes during
the column-mapping step, but still shows you the mapping to confirm. That way the server
stays bank-agnostic, and each user's bank knowledge lives in their own config.

`date_source` picks which date becomes the transaction date when a statement has two:
the bank's **posting** (booking) date, or the **transaction** date when you actually
paid. Card purchases often post a day or two later, and the card date is often only
inside the description. If `date_source` is unset, Claude asks during import whenever a
statement has both. Setting it is recommended. For rows without a reference ID, the
date is part of duplicate detection, so switching between the two dates across imports
could let the same row in twice.

## Install

The server runs with `uvx`, so there's nothing to install globally. Point
`GNUCASH_MCP_CONFIG` at your book TOML, using an absolute path.

**Claude Code:**

```bash
claude mcp add gnucash -e GNUCASH_MCP_CONFIG=/absolute/path/to/personal.toml -- uvx gnucash-mcp
```

**Claude Desktop:** add this to `claude_desktop_config.json` (on macOS it's in
`~/Library/Application Support/Claude/`) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "gnucash": {
      "command": "uvx",
      "args": ["gnucash-mcp"],
      "env": {"GNUCASH_MCP_CONFIG": "/absolute/path/to/personal.toml"}
    }
  }
}
```

If Claude Desktop can't find `uvx`, replace `"uvx"` with its full path (`which uvx`).

## Tools and prompts

Tools:

- `list_accounts`
- `get_account(fullname)`
- `list_transactions(fullname, start_date, end_date, limit)`
- `create_account(fullname, account_type, currency=None, description="",
  placeholder=False)`: the parent must exist; the currency defaults to the parent's
- `find_transactions(search, limit=5)`: find transactions in any account whose
  description, notes or number contain the word, newest first.
- `copy_transaction(guid, post_date, external_id=None, notes=None)`: add a copy of an
  existing transaction with all its splits (also more than two); only the date, number
  and notes are new. Used by the clone options for entries `add_transaction` can't write.
- `add_transaction(post_date, description, counter_account, amount, account=None,
  external_id=None, notes=None, force=False, quantity=None)`
- `update_transaction(guid, post_date=None, description=None, external_id=None,
  notes=None, counter_account=None, amount=None, account=None, quantity=None)`: change
  an existing transaction; fields left out stay as they are, and an empty `external_id`
  or `notes` clears it. Get the `guid` from `list_transactions`. Changing `amount`,
  `quantity` or `counter_account` needs a two-split transaction and moves both splits,
  so it stays balanced; `account` (default: the config's `bank_account`) says which
  side `amount` refers to. When only `amount` changes on a transaction with a commodity
  counter account, the units stay and the price changes. To change the amount of a
  copy, call this on the guid `copy_transaction` returns.
- `delete_transaction(guid)`: permanently delete a transaction and its splits, and
  return what was deleted.
- `import_transactions(rows, dry_run=True)`: batch import. Each row gets a status of
  `new`, `added` or `duplicate`, and duplicates include the existing transactions they
  match.

If `account` is omitted, it defaults to the config's `bank_account`. Read tools and dry
runs open the book read-only. Writes open it for writing. A batch import opens it once, so
it creates one backup file.

Prompt:

- `import_bank_statement`: imports a bank CSV with a preview table and one batch write
  (in Claude Code, run `/mcp__gnucash__import_bank_statement`).
- `review_bank_statement`: imports a bank CSV one row at a time, waiting for your decision
  on each row (in Claude Code, run `/mcp__gnucash__review_bank_statement`).
- `clone_transaction`: asks you for a search word, finds the newest similar entry, and
  adds a new one with the same accounts and description after you confirm the date and
  amount (in Claude Code, run `/mcp__gnucash__clone_transaction`).

### Buying a commodity (gold, stocks)

When the counter account holds a non-currency commodity, such as gold coins or shares,
pass `quantity`: the units bought or sold, as a positive number. `amount` stays the
value on the bank account in the bank's currency, and the price per unit is
`amount / quantity`. The transaction keeps the bank account's currency; the bank split
gets `-amount` and the counter split gets the value `-amount` and the quantity
`quantity` (negative when `amount` is positive, a sale):

```python
add_transaction(
    post_date=date(2026, 9, 29),
    description="Buy 5 from Anis",
    counter_account="Assets:Gold English Coins 21 carat",  # commodity XGL21-8
    amount=Decimal("-3330"),                               # JOD out of the bank
    quantity=Decimal("5"),                                 # 666 JOD per coin
    account="Assets:Bank Accounts:ArabBank Jordan",        # commodity JOD
)
```

`quantity` is required for such a counter account and rejected for one in the bank
account's currency. It must be positive and no finer than the commodity's fraction.
`import_transactions` rows take the same `quantity` field. Converting between two
currencies is still not supported.

## Importing a bank statement

1. Upload the CSV and run the `import_bank_statement` prompt.
2. Claude works out the CSV layout from its header and first rows, for any bank: the
   delimiter, the date column and format, the description column, and the amounts. It
   handles both one signed amount column and separate money-out and money-in columns,
   and either decimal separator (`1,234.56` or `1.234,56`). It shows you the mapping
   with one parsed row, and waits for you to confirm before parsing everything. Money
   out becomes a negative amount and money in a positive one. Each row gets a short,
   readable description (e.g. `Zalatimo Sweets`), and the bank's original text is kept
   verbatim in the transaction's notes. Claude checks whether the file lists the newest
   row first, and if so reads it from the bottom up, so rows are written oldest first
   in the bank's own order (same-day rows are not re-sorted). If you'd rather keep the bank text as the
   description, say so when confirming the mapping. Then Claude picks a category account
   for each row.
3. Claude calls `import_transactions(rows, dry_run=True)` and shows a preview with each
   row marked new or duplicate.
4. After you confirm, Claude calls `import_transactions(rows, dry_run=False)`. The new
   rows are written in one go, and duplicates are left out.
5. For each duplicate, Claude asks whether to force it or skip it. Forcing calls
   `add_transaction(..., force=True)`. This covers, for example, a genuine second
   identical purchase on the same day.

Each transaction is recorded in the bank account's own currency, so a EUR account in a USD
book gets EUR transactions. The category account must use the same currency, because
currency conversion isn't supported. If the category you want is missing, Claude offers to create it with
`create_account` and does so once you confirm.

Nothing is written if any row has an unknown account, mixes currencies, or has an amount
with more decimals than the bank account's currency allows.

### Reviewing row by row

Run `review_bank_statement` instead. After the same currency, parsing and category steps,
Claude shows one row at a time (`Row i/N`) with its Num (the bank reference stored as the
transaction number), proposed category and whether it is new or a duplicate. For each row you choose one of:

- **Accept:** write the row now, with `import_transactions([row], dry_run=False)`.
- **Change:** edit the category, description or amount, then review the row again.
- **Skip:** write nothing.
- **Force:** for duplicates only; write it anyway with `add_transaction(..., force=True)`.
- **Accept all remaining:** write the remaining new rows in one batch. Duplicates are
  still reviewed one at a time.
- **Stop:** end the review and get a summary.

Rows you approve are written immediately. If you stop halfway, they stay in the book, and
a re-run shows them as duplicates. Each approval is a separate write, so with
`do_backup = true` every approved row creates a backup file. Set `do_backup = false` in
the book TOML if you don't want that.

## Idempotent writes

```python
from decimal import Decimal
from datetime import date
from pathlib import Path
from gnucash_storage import (
    NewTransaction,
    add_transaction,
    load_config,
    open_configured_book,
)

config = load_config(Path("books/development-book.toml"))
with open_configured_book(config, readonly=False) as book:
    added = add_transaction(
        book,
        NewTransaction(
            post_date=date(2025, 11, 28),
            description="TEST BISTRO REST.",
            account="Assets:Current Assets:Savings Account",
            counter_account="Expenses:Dining",
            amount=Decimal("-36.00"),  # money out of the bank account
        ),
        force=False,  # set force=True to bypass duplicate checking
    )
```

A row can carry an optional `external_id`: a unique reference from the bank, such as a
card retrieval reference number (`REF 606006000101`), an OFX `FITID`, or a bank
reference. It's stored in the transaction's **Num** field. During import, Claude looks
for such a reference and includes it in the column mapping you confirm.

An existing transaction on the same bank account counts as a duplicate when:

- it has the same `external_id`, whatever its date or description, so a bank that
  reformats descriptions between exports doesn't cause a double import; or
- it has the same post date, bank text and amount (in the account's currency),
  unless both sides have IDs and they differ. Two identical purchases on the same day
  with different card references are both imported, while transactions added before
  IDs were used are still recognised.

The *bank text* is the transaction's notes, or its description when it has no notes.
Matching on the bank's own text rather than the cleaned-up description means a
description worded differently on a later import still counts as a duplicate.
Transactions imported before notes were used, which kept the bank text as the
description, still match too.

When `force=False` (the default), `add_transaction` adds nothing and returns `False` for
duplicates. Pass `force=True` to bypass duplicate checking and force insertion.

If your book has GnuCash's *Use Split Action Field for Number* option enabled, the
register shows the split action in the Num column. The reference is still stored on the
transaction.

## Development

The server itself needs only `mcp` and `piecash`. Development tools and notebook
libraries are optional extras:

```bash
uv sync --extra dev                    # tests, ruff, mypy
uv sync --extra dev --extra notebooks  # plus marimo, Jupyter, polars, pandas, openai
```

Run the server from a checkout:

```bash
GNUCASH_MCP_CONFIG=books/example.toml uv run gnucash-mcp
```

Inside this repository, `.mcp.json` already registers the server for Claude Code as
`gnucash`, using `books/development-book.toml`. Approve it when Claude Code prompts, then check it
with `claude mcp get gnucash`.

## Notebooks

The notebooks need the `notebooks` extra (`uv sync --extra notebooks`).

Start the Marimo notebook server / editor:

```bash
uv run marimo edit notebooks/
```

Or run a notebook in read-only app mode:

```bash
uv run marimo run notebooks/gnucash_exploration.py
```

`notebooks/statement_mapping.py` checks the generic CSV mapping against two sample
statements in `notebooks/samples/`:

- `us_signed_amount.csv`: comma-separated, US dates, one signed Amount column
- `eu_debit_credit.csv`: semicolon-separated, German headers, dd.mm.yyyy dates, separate
  Soll/Haben columns, decimal comma

For each sample, the notebook applies the mapping Claude would confirm and imports the
rows into a throwaway book. It then checks the signs, the balance, and that a second
import reports every row as a duplicate:

```bash
uv run marimo edit notebooks/statement_mapping.py
```

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

To run the same checks as CI before every `git push`, enable the tracked pre-push hook once per clone:

```bash
git config core.hooksPath .githooks
```

Skip it for a single push with `git push --no-verify`.

## Releasing

Releases are published to PyPI by `.github/workflows/publish.yml`, using
[trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is
needed.

One-time setup on PyPI: go to *Your account > Publishing > Add a new pending
publisher* and enter:

| Field | Value |
|---|---|
| PyPI project name | `gnucash-mcp` |
| Owner | `tillawy` |
| Repository name | `gnucash-mcp` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

To release:

1. Bump `version` in `pyproject.toml`, then commit and push.
2. Create a GitHub release whose tag is `v` plus that version:

   ```bash
   gh release create v0.1.0 --generate-notes
   ```

The workflow checks that the tag matches the version, runs the tests, ruff and mypy,
builds the package and uploads it. To require a manual approval before each upload, add
a required reviewer to the `pypi` environment under *Settings > Environments*.

To check a build locally without publishing:

```bash
uv build
uvx twine check --strict dist/*
```
