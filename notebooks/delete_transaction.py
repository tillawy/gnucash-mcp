import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Testing the `delete_transaction` tool

    This notebook calls the MCP server's `delete_transaction` tool against a
    throwaway book in a temporary directory, so your real book is never touched.
    The book has `bank_account = "Assets:Bank"` and two transactions.
    """)
    return


@app.cell
def _():
    import os
    import tempfile
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    import piecash

    from gnucash_mcp import server

    workdir = Path(tempfile.mkdtemp(prefix="delete-transaction-"))
    book_path = workdir / "scratch.gnucash"
    with piecash.create_book(sqlite_file=str(book_path), currency="USD") as _book:
        _usd = _book.default_currency
        _assets = piecash.Account("Assets", "ASSET", _usd, parent=_book.root_account)
        _expenses = piecash.Account(
            "Expenses", "EXPENSE", _usd, parent=_book.root_account
        )
        piecash.Account("Bank", "BANK", _usd, parent=_assets)
        piecash.Account("Food", "EXPENSE", _usd, parent=_expenses)
        piecash.Account("Dining", "EXPENSE", _usd, parent=_expenses)
        _book.save()

    config_path = workdir / "book.toml"
    config_path.write_text(
        f'book_path = "{book_path}"\nbank_account = "Assets:Bank"\ndo_backup = false\n'
    )

    os.environ[server.CONFIG_ENV_VAR] = str(config_path)
    server.book_config.cache_clear()

    server.add_transaction(
        date(2025, 11, 28),
        "TEST BISTRO REST.",
        "Expenses:Food",
        Decimal("-36.00"),
        external_id="REF1",
        notes="CARD PURCHASE TEST BISTRO REST. AMMAN",
    )
    server.add_transaction(
        date(2025, 11, 29), "Coffee", "Expenses:Food", Decimal("-4.50")
    )
    return Decimal, date, server


@app.cell
def _(server):
    def show():
        """Return the bank account's transactions as plain rows."""
        return [
            (
                t.guid[:8],
                str(t.post_date),
                t.description,
                t.num,
                t.notes,
                {s.account: str(s.value) for s in t.splits},
            )
            for t in server.list_transactions("Assets:Bank")
        ]

    return (show,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Before
    """)
    return


@app.cell
def _(server, show):
    (show(), {a.fullname: str(a.balance) for a in server.list_accounts()})
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Delete one transaction

    The tool returns the transaction as it was before deletion.
    """)
    return


@app.cell
def _(server):
    _guid = server.list_transactions("Assets:Bank")[0].guid
    deleted = server.delete_transaction(_guid)
    deleted
    return (deleted,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## After

    One transaction is left, and the balances drop back: Bank -36.00, Food 36.00.
    """)
    return


@app.cell
def _(deleted, server, show):
    (show(), {a.fullname: str(a.balance) for a in server.list_accounts()})
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Error cases

    Deleting the same transaction again, or an unknown guid, should raise.
    """)
    return


@app.cell
def _(deleted, server):
    outcomes = {}
    _targets = {"already deleted": deleted.guid, "unknown guid": "0" * 32}
    for _label, _guid in _targets.items():
        try:
            server.delete_transaction(_guid)
        except KeyError as error:
            outcomes[_label] = f"KeyError: {error}"
        else:
            outcomes[_label] = "NO ERROR (unexpected)"
    outcomes
    return (outcomes,)


if __name__ == "__main__":
    app.run()
