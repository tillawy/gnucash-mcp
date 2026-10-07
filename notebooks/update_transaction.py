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
    # Testing the `update_transaction` tool

    This notebook calls the MCP server's `update_transaction` tool against a
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

    workdir = Path(tempfile.mkdtemp(prefix="update-transaction-"))
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
def _(show):
    show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Change only the description and notes

    Other fields must stay as they were. An empty string clears `notes`.
    """)
    return


@app.cell
def _(server, show):
    _guid = server.list_transactions("Assets:Bank")[1].guid
    server.update_transaction(_guid, description="Test Bistro", notes="")
    show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Change the amount and the counter account

    `account` defaults to the configured `bank_account`. Both splits must move to
    the new amount, so the transaction stays balanced.
    """)
    return


@app.cell
def _(Decimal, server, show):
    _guid = server.list_transactions("Assets:Bank")[1].guid
    server.update_transaction(
        _guid, counter_account="Expenses:Dining", amount=Decimal("-40.25")
    )
    show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Change the date and the transaction number
    """)
    return


@app.cell
def _(date, server, show):
    _guid = server.list_transactions("Assets:Bank")[0].guid
    server.update_transaction(_guid, post_date=date(2025, 12, 2), external_id="REF9")
    show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Balances after

    Bank should be -44.75, Dining 40.25 and Food 4.50.
    """)
    return


@app.cell
def _(server):
    {a.fullname: str(a.balance) for a in server.list_accounts()}
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Error cases

    Each of these should raise, and leave the book unchanged.
    """)
    return


@app.cell
def _(Decimal, server):
    _guid = server.list_transactions("Assets:Bank")[0].guid
    _cases = {
        "unknown guid": lambda: server.update_transaction("0" * 32, description="x"),
        "unknown counter": lambda: server.update_transaction(
            _guid, counter_account="Nope"
        ),
        "too many decimals": lambda: server.update_transaction(
            _guid, amount=Decimal("-1.005")
        ),
        "account not on transaction": lambda: server.update_transaction(
            _guid, amount=Decimal("-1"), account="Assets"
        ),
    }
    outcomes = {}
    for _label, _call in _cases.items():
        try:
            _call()
        except (KeyError, ValueError) as error:
            outcomes[_label] = f"{type(error).__name__}: {error}"
        else:
            outcomes[_label] = "NO ERROR (unexpected)"
    outcomes
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Book unchanged after the errors
    """)
    return


@app.cell
def _(show):
    show()
    return


if __name__ == "__main__":
    app.run()
