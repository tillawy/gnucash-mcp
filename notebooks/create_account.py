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
    # Testing the `create_account` tool

    This notebook calls the MCP server's `create_account` tool against a throwaway
    book in a temporary directory, so your real book is never touched.
    """)
    return


@app.cell
def _():
    import os
    import tempfile
    from pathlib import Path

    import piecash

    from gnucash_mcp import server

    workdir = Path(tempfile.mkdtemp(prefix="create-account-"))
    book_path = workdir / "scratch.gnucash"
    with piecash.create_book(sqlite_file=str(book_path), currency="USD") as _book:
        _assets = piecash.Account(
            "Assets", "ASSET", _book.default_currency, parent=_book.root_account
        )
        piecash.Account("Bank", "BANK", _book.default_currency, parent=_assets)
        _book.save()

    config_path = workdir / "book.toml"
    config_path.write_text(f'book_path = "{book_path}"\ndo_backup = false\n')

    os.environ[server.CONFIG_ENV_VAR] = str(config_path)
    server.book_config.cache_clear()
    return (server,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Accounts before
    """)
    return


@app.cell
def _(server):
    [(a.fullname, a.type, a.commodity) for a in server.list_accounts()]
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Create accounts

    - a top-level account in the book's default currency
    - a child that inherits its parent's currency
    - a top-level account in another currency
    - a placeholder with a description
    """)
    return


@app.cell
def _(server):
    created = [
        server.create_account("Expenses", "EXPENSE", placeholder=True),
        server.create_account("Expenses:Dining", "EXPENSE", description="Eating out"),
        server.create_account("Savings EUR", "BANK", currency="EUR"),
        server.create_account("Assets:Cash", "CASH"),
    ]
    [(a.fullname, a.type, a.commodity) for a in created]
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Accounts after
    """)
    return


@app.cell
def _(server):
    [(a.fullname, a.type, a.commodity) for a in server.list_accounts()]
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Error cases

    Each of these should raise, and leave the book unchanged.
    """)
    return


@app.cell
def _(server):
    cases = {
        "duplicate": ("Expenses:Dining", "EXPENSE"),
        "unknown type": ("Expenses:Rent", "NOPE"),
        "root type": ("Expenses:Rent", "ROOT"),
        "empty name": ("Expenses:", "EXPENSE"),
        "missing parent": ("Missing:Rent", "EXPENSE"),
    }
    outcomes = {}
    for label, (fullname, account_type) in cases.items():
        try:
            server.create_account(fullname, account_type)
        except (KeyError, ValueError) as error:
            outcomes[label] = f"{type(error).__name__}: {error}"
        else:
            outcomes[label] = "NO ERROR (unexpected)"
    outcomes
    return


if __name__ == "__main__":
    app.run()
