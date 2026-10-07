import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Book Explore")


@app.cell
def _():
    import marimo as mo
    from datetime import date, datetime
    from decimal import Decimal
    from pathlib import Path
    import tempfile
    import piecash

    from gnucash_storage import (
        AccountInfo,
        BookConfig,
        NewTransaction,
        SplitInfo,
        TransactionInfo,
        add_transaction,
        get_account,
        list_accounts,
        list_transactions,
        open_configured_book,
        transaction_exists,
    )

    return (
        BookConfig,
        Decimal,
        NewTransaction,
        Path,
        add_transaction,
        date,
        list_accounts,
        list_transactions,
        mo,
        open_configured_book,
        piecash,
        tempfile,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Understanding `gnucash_storage/book.py`

    This interactive notebook walks through the core domain logic, data models, and storage operations implemented in `src/gnucash_storage/book.py`.

    ### Key Objectives & Utility
    1. **Safe Book Access**: Context manager pattern (`open_configured_book`) ensuring sessions and file locks are cleanly released.
    2. **Immutable Data Contracts**: Plain dataclasses (`AccountInfo`, `TransactionInfo`, `SplitInfo`, `NewTransaction`) that decouple caller logic from raw SQLAlchemy / piecash models.
    3. **Idempotent Write Operations**: Deterministic insertion with duplicate prevention (`transaction_exists`, `add_transaction`).
    4. **Targeted Querying**: High-efficiency filtered account and transaction lookups (`list_accounts`, `get_account`, `list_transactions`).
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Environment & Book Setup

    Choose between initializing a clean temporary demonstration book or targeting an existing GnuCash file.
    """)
    return


@app.cell
def _(mo):
    book_mode = mo.ui.radio(
        options=["Create Fresh Temporary Book", "Use Existing File (db/mcp.gnucash)"],
        value="Create Fresh Temporary Book",
        label="Select Book Source",
    )
    book_mode
    return (book_mode,)


@app.cell
def _(BookConfig, Path, book_mode, piecash, tempfile):
    def _init_demo_book() -> BookConfig:
        temp_dir = tempfile.mkdtemp()
        path = Path(temp_dir) / "demo.gnucash"
        with piecash.create_book(sqlite_file=str(path), currency="USD") as book:
            usd = book.default_currency
            assets = piecash.Account("Assets", "ASSET", usd, parent=book.root_account)
            expenses = piecash.Account("Expenses", "EXPENSE", usd, parent=book.root_account)
            income = piecash.Account("Income", "INCOME", usd, parent=book.root_account)
            liabilities = piecash.Account("Liabilities", "LIABILITY", usd, parent=book.root_account)

            piecash.Account("Checking", "BANK", usd, parent=assets)
            piecash.Account("Savings", "BANK", usd, parent=assets)
            piecash.Account("Groceries", "EXPENSE", usd, parent=expenses)
            piecash.Account("Dining", "EXPENSE", usd, parent=expenses)
            piecash.Account("Salary", "INCOME", usd, parent=income)
            piecash.Account("Credit Card", "CREDIT", usd, parent=liabilities)
            book.save()
        return BookConfig(book_path=path, do_backup=False)

    existing_path = Path("db/mcp.gnucash")
    if book_mode.value == "Use Existing File (db/mcp.gnucash)" and existing_path.exists():
        active_config = BookConfig(book_path=existing_path.resolve(), do_backup=False)
    else:
        active_config = _init_demo_book()

    active_config
    return (active_config,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Account Inspection (`list_accounts` & `get_account`)

    `list_accounts` retrieves all accounts across the hierarchy, computing net balances adjusted for account types.
    """)
    return


@app.cell
def _(active_config, list_accounts, mo, open_configured_book):
    with open_configured_book(active_config, readonly=True) as book:
        all_accounts = list_accounts(book)

    account_table_data = [
        {
            "Full Name": acc.fullname,
            "Type": acc.type,
            "Commodity": acc.commodity,
            "Balance": f"{acc.balance:,.2f}",
        }
        for acc in all_accounts
    ]

    mo.ui.table(account_table_data, label="Accounts in Book")
    return (all_accounts,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Idempotent Transaction Ingestion (`add_transaction`)

    `add_transaction` ensures data integrity by enforcing balanced dual-entry splits and preventing duplicate submissions on identical dates, descriptions, accounts, and amounts.
    """)
    return


@app.cell
def _(all_accounts, date, mo):
    account_names = [acc.fullname for acc in all_accounts]
    default_primary = "Assets:Checking" if "Assets:Checking" in account_names else account_names[0]
    default_counter = "Expenses:Dining" if "Expenses:Dining" in account_names else account_names[-1]

    tx_date_input = mo.ui.date(value=date.today(), label="Posting Date")
    tx_desc_input = mo.ui.text(value="Supermarket Supplies", label="Description")
    tx_account_select = mo.ui.dropdown(options=account_names, value=default_primary, label="Primary Account")
    tx_counter_select = mo.ui.dropdown(options=account_names, value=default_counter, label="Counter Account")
    tx_amount_input = mo.ui.number(value=-45.50, step=0.01, label="Amount (Primary Leg)")
    submit_button = mo.ui.run_button(label="Add Transaction")

    mo.vstack([
        mo.hstack([tx_date_input, tx_desc_input]),
        mo.hstack([tx_account_select, tx_counter_select, tx_amount_input]),
        submit_button,
    ])
    return (
        account_names,
        default_primary,
        submit_button,
        tx_account_select,
        tx_amount_input,
        tx_counter_select,
        tx_date_input,
        tx_desc_input,
    )


@app.cell
def _(
    Decimal,
    NewTransaction,
    active_config,
    add_transaction,
    mo,
    open_configured_book,
    submit_button,
    tx_account_select,
    tx_amount_input,
    tx_counter_select,
    tx_date_input,
    tx_desc_input,
):
    def _():
        result_callout = None
        if submit_button.value:
            new_tx = NewTransaction(
                post_date=tx_date_input.value,
                description=tx_desc_input.value,
                account=tx_account_select.value,
                counter_account=tx_counter_select.value,
                amount=Decimal(str(tx_amount_input.value)),
            )
            with open_configured_book(active_config, readonly=False) as book:
                added = add_transaction(book, new_tx)

            if added:
                result_callout = mo.md(
                    f"**Success:** Transaction `{new_tx.description}` of `{new_tx.amount}` posted successfully."
                )
            else:
                result_callout = mo.md(
                    f"**Duplicate Detected:** Transaction `{new_tx.description}` with amount `{new_tx.amount}` on `{new_tx.post_date}` already exists in `{new_tx.account}`. Operation skipped idempotently."
                )
        return result_callout


    _()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Account Ledger & Transaction Querying (`list_transactions`)

    `list_transactions` queries split entries touching an account, ordered chronologically descending, with optional date bounds and limit controls.
    """)
    return


@app.cell
def _(account_names, default_primary, mo):
    query_account = mo.ui.dropdown(options=account_names, value=default_primary, label="Account to Inspect")
    query_limit = mo.ui.slider(start=1, stop=50, step=1, value=10, label="Result Limit")
    mo.hstack([query_account, query_limit])
    return query_account, query_limit


@app.cell
def _(
    active_config,
    list_transactions,
    mo,
    open_configured_book,
    query_account,
    query_limit,
):
    def _():
        with open_configured_book(active_config, readonly=True) as book:
            tx_list = list_transactions(
                book,
                fullname=query_account.value,
                start=None,
                end=None,
                limit=query_limit.value,
            )

        tx_rows = []
        for t in tx_list:
            splits_repr = ", ".join(f"{s.account}: {s.value:+,.2f}" for s in t.splits)
            tx_rows.append({
                "Date": str(t.post_date),
                "Description": t.description,
                "GUID": t.guid[:8] + "...",
                "Splits": splits_repr,
            })
        return mo.ui.table(tx_rows, label=f"Recent Transactions for {query_account.value}")


    _()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Architectural Summary of `book.py`

    | Component | Role & Responsibility | Benefit |
    |---|---|---|
    | `open_configured_book` | Safe session lifecycle management with lock bypass for readonly mode | Prevents database lock collisions and resource leaks |
    | `AccountInfo` / `TransactionInfo` | Decoupled, read-only DTO snapshots | Clean interface boundaries, thread-safe, no detached ORM issues |
    | `NewTransaction` | Input contract for 2-split balanced postings | Guarantees balanced transactions and prevents invalid partial writes |
    | `transaction_exists` | 24-hour window deduplication check | Protects against duplicate imports and re-executions |
    | `list_transactions` | Joined index-aware query with limits & date filters | Fast retrieval without loading complete transaction histories |
    """)
    return


if __name__ == "__main__":
    app.run()
