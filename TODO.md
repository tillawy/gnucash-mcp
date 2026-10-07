# TODO: make the importer work for everyone

Goal: any GnuCash user, with any bank, can install the server and import a statement.
Keep the server bank-agnostic. It takes structured rows, and Claude handles
bank-specific parsing, guided by prompts and per-book config.

## Priority: these break a new user's first import

- [x] **Generic CSV mapping in the prompts.** `_statement_setup_steps` in
  `src/gnucash_mcp/server.py` hard-codes one bank's layout: `Date`, `Details`, `Debit`,
  `Credit`, dd/mm/yyyy. Instead, Claude should read the header and work out:
  - which columns hold the date, description and amount
  - the date format
  - the sign convention (separate Debit/Credit columns, or one signed Amount column)
  - the delimiter

  It then shows the mapping for the user to confirm before parsing, the same way it
  already confirms the currency.
  Done: the prompts now ask Claude to infer and confirm the mapping.
  `notebooks/statement_mapping.py` checks two sample layouts in `notebooks/samples/`.
- [x] **Use the bank account's own currency, not the book default.** `_build_transaction`
  and `_check_precision` in `src/gnucash_storage/book.py` use `book.default_currency`. A
  EUR bank account in a USD book gets wrong transactions. Use the bank account's
  commodity for the transaction currency and the precision check, and add a test with an
  account in a different currency.
  Done:
  - the transaction currency is now the bank account's commodity
  - `_check_amount` checks precision against it, and rejects rows whose category
    account uses a different currency
  - duplicate detection compares the split quantity, i.e. the amount in the account's
    own currency
- [ ] **Currency conversion for mixed-currency rows.** Today a EUR bank row with a USD
  category account is rejected. piecash would otherwise silently record a 1:1 rate and
  write it to the price database. Options:
  - an optional `counter_amount` per row
  - converting with the book's price database, and rejecting when no price exists
- [x] **Document the SQL-only backend and detect XML books.** piecash cannot open
  GnuCash's default XML format; it only supports SQLite, PostgreSQL and MySQL books.
  - README: say this up front, with the fix (in GnuCash, *File → Save As → sqlite3*).
  - `load_config` / `open_configured_book`: detect an XML or gzipped XML file and raise a
    clear error.
  Done:
  - `open_configured_book` checks the file header and raises a `ValueError` for plain
    or gzipped XML (and any other non-SQLite file), with the *Save As > sqlite3* fix
  - the README has a "Requirements: a SQLite book" section

  The check lives in `open_configured_book` rather than `load_config`, so an MCP client
  shows the message in the chat instead of the server silently failing to start.

## Next

- [x] **Optional reference ID for duplicate detection.** Add `external_id: str | None` to
  `NewTransaction` and `ImportRow`, and store it in the transaction's `num` field.
  - Duplicate check: match on `external_id` when present, otherwise fall back to the
    current (date, description, value) key.
  - Claude fills it from whatever the bank provides: a card RRN (`REF 606006000101`), an
    OFX `FITID`, or a bank reference.
  - Benefit: a changed description no longer causes a double import.
  Done:
  - `external_id` is on `NewTransaction`, `ImportRow` and the `add_transaction` tool,
    and is stored in `num`
  - `_matching_transactions` matches on the ID at any date, or on
    (date, description, amount) unless both IDs exist and differ
  - `find_duplicates(book, new)` now takes a `NewTransaction`
  - the prompts ask Claude to find a reference column or ID in the description as part
    of the mapping

## Distribution

- [x] **Trim runtime dependencies.** Move `pandas`, `pandas-stubs`, `numpy`,
  `langchain`, `openai`, `python-dotenv` and `ipykernel` from `[project.dependencies]` to
  dev or notebook extras. They're only used by the notebooks.
  Done:
  - the runtime dependencies are just `mcp` and `piecash` (34 installed packages instead
    of 101)
  - dropped `langchain`, `numpy` and the `mcp[cli]` extra, since nothing imports them
  - the notebook libraries moved to a new `notebooks` extra
  - capped `mcp` below 2: a fresh install was resolving mcp 2.x, where `FastMCP` no
    longer exists, so the server couldn't start
- [ ] **Migrate to mcp 2.** `FastMCP` became `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`), and other APIs changed; see the
  migration guide at
  https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver.
  Then lift the `mcp<2` cap in `pyproject.toml`.
- [ ] **Publish to PyPI** so users can run `uvx gnucash-mcp` without cloning the repo.
  - Fill in the `pyproject.toml` description, license and URLs.
  - README: add install instructions for Claude Desktop and Claude Code that use `uvx`.
  Ready to publish:
  - package renamed to `gnucash-mcp`, so `uvx gnucash-mcp` works directly
  - import package `mcp_cli` renamed to `gnucash_mcp`, because the existing `mcp-cli`
    package on PyPI installs a module with the same name
  - MIT `LICENSE`, description, keywords and classifiers added
  - the sdist is limited to the source, tests, example config, README and LICENSE
  - README rewritten for users: uvx install for Claude Code and Claude Desktop,
    Development and Releasing sections
  - `twine check --strict` passes, and the server runs through `uvx --from <wheel>`

  Still to do:
  - [x] Add `[project.urls]` (Homepage, Repository, Issues) once the GitHub repo exists.
    Done: https://github.com/tillawy/gnucash-mcp (private for now; make it public
    before the PyPI upload so the links work).
  - [x] Trusted publishing workflow: `.github/workflows/publish.yml` publishes when a
    GitHub release `v<version>` is published, after the tests, ruff and mypy pass.
    Actions are pinned to commit SHAs, and only the publish job gets `id-token`.
  - [ ] One-time: add the pending publisher on PyPI (see README "Releasing").
  - [ ] Make the repo public, then run `gh release create v0.1.0 --generate-notes`.
- [x] **Per-book bank notes in the config.** Add an optional `statement_notes` key to the
  book TOML, e.g. "Details contains `REF <id>` and the card date as YY/MM/DD". It gets
  injected into the import prompts, so each user's bank quirks live in their config, not
  in code.
  Done:
  - `BookConfig.statement_notes` is read from the TOML, including multi-line strings
  - `_statement_setup_steps` adds the notes to the mapping step as hints, and still asks
    for the mapping to be confirmed
  - `books/development-book.toml` has the Arab Bank Jordan notes (card RRN, YY/MM/DD card date,
    posting date)

## Descriptions

- [x] **Clean description, keep the raw text.** Claude suggests a short description
  (e.g. `Zalatimo Sweets`) and the raw bank narrative goes in the transaction's notes.
  - Decide how this interacts with duplicate detection. Once the reference ID exists,
    dedupe on it rather than on the cleaned description.

  Done:
  - `notes` is on `NewTransaction`, `ImportRow` and the `add_transaction` tool, is
    stored in GnuCash's transaction notes, and is returned as `TransactionInfo.notes`
  - the date/text/amount match compares the raw bank text (notes, else
    description), never the cleaned description, because Claude may clean the same
    text differently on another run
  - the prompts ask Claude for a short description and the verbatim text in notes,
    shown in the mapping confirmation
- [x] **Posting date vs. card transaction date.** Card narratives often carry the real
  purchase date, which differs from the statement's posting date. Make the choice a
  config option or ask during import.
  Done:
  - `date_source = "posting" | "transaction"` in the book TOML, validated by
    `load_config`
  - the mapping step tells Claude which date to use. When `date_source` is unset,
    Claude asks only if the statement has a second date, and suggests setting it,
    since changing the date choice between imports would break duplicate detection
    for rows without reference IDs

## Maintenance risk

- [x] **piecash is barely maintained** and still depends on SQLAlchemy 1.4. Keep all
  piecash usage inside `gnucash_storage` (already the case) so the backend can be swapped
  later. Track upstream and SQLAlchemy 2 support.
  In place:
  - `tests/test_architecture.py` fails if anything outside `gnucash_storage` imports
    piecash, SQLAlchemy or sqlalchemy-utils. The MCP server only passes the open book
    object through to `gnucash_storage` functions.
  - `piecash>=1.2.1,<2`: piecash 2.0 is planned alongside SQLAlchemy 2 support, and its
    API may change.
  - Tested on Python 3.13 and 3.14: the full test suite, plus the server run through
    `uvx` from the wheel. 3.14 is declared in the classifiers.

  Upstream status (checked 2026-09-28):
  - piecash 1.2.1 (2024-07-14) is the latest release, and it requires
    `SQLAlchemy<1.5`. The repository is not archived; last push 2026-07-27.
  - SQLAlchemy's last 1.4 release is 1.4.54 (2024-09-05); the current release is 2.1.
  - SQLAlchemy 2 support is still open:
    https://github.com/sdementen/piecash/issues/218,
    https://github.com/sdementen/piecash/issues/248, and the piecash 2.0 plan in
    https://github.com/sdementen/piecash/issues/231.

  Watch:
  - [ ] A piecash release with SQLAlchemy 2 support: raise the `<2` cap and re-run
    the tests.
  - [ ] New Python versions: on 3.14 piecash emits `SyntaxWarning: "\*" is an invalid
    escape sequence` (piecash/core/book.py:343). Python plans to make that an error,
    which would break importing piecash. Run the suite on each new Python before
    declaring it supported.
  - [ ] If piecash stalls for good, candidates to replace it inside `gnucash_storage`
    are GnuCash's official Python bindings (hard to install outside distro packages) or
    direct SQL against the GnuCash schema.

## Won't build

- **Parsers for OFX, QIF, MT940 or camt.053.** GnuCash imports these natively. Point
  users to GnuCash's importer when their bank offers them. This project focuses on messy
  CSVs, PDFs and statement screenshots.
- **Cleanup rules for one bank's descriptions in code.** Bank-specific knowledge belongs
  in the per-book `statement_notes` and in Claude's parsing.

## Data hygiene

- [x] **Replace `notebooks/demo-data-1.csv` with realistic synthetic data.** Right now:
  - one RRN ends in the letter `O` instead of zero
  - narrative dates don't match the posting dates
  - the RRN day-of-year doesn't match the narrative date

  The generic-mapping samples already exist: `notebooks/samples/us_signed_amount.csv`
  and `eu_debit_credit.csv`.
  Done:
  - the file is a synthetic Jordanian JOD statement in the same columns. Card
    references encode the card date (year digit, day of year, UTC hour), each card
    row posts a day after its card date, and the running balance is consistent.
  - it covers a CliQ transfer in, a fee without a reference, and two identical
    same-day purchases with different references
  - `tests/test_demo_data.py` checks all of this, and that any IBAN is fake (check
    digits `00`)
- [x] **Remove the old demo data from git history before making the repo public.** The
  previous `notebooks/demo-data.csv` contained what looks like a real IBAN and a
  person's name, and possibly real card references. The saved outputs of the notebooks
  and an earlier committed copy of the GnuCash book had the same data.
  Done (2026-09-29), before any push:
  - `git filter-repo` removed `db/mcp.gnucash`, `.output.txt` and every earlier
    version of `notebooks/demo-data.csv`, `gnucash_exploration.ipynb` and
    `langchain-ocr.ipynb`, and replaced the IBAN, the name and 14 real card
    references everywhere else with synthetic values
  - the clean files were re-added, and all commits were re-signed with their
    original dates
  - verified: none of the 26 sensitive strings appears in any commit or tracked file
  - `.gitignore` now ignores books, backups, logs, lock files, `statements/` and scans
    in `notebooks/`
- [ ] Delete the pre-rewrite backup once you're happy with the result:
  `../gnucash-mcp-server-history-backup.git`. It still contains the sensitive data.
