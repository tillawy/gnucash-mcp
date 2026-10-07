# /// script
# dependencies = [
#     "marimo>=0.25.0",
# ]
# ///
import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium", app_title="Statement Mapping")


@app.cell
def _():
    import tempfile
    from dataclasses import asdict, dataclass
    from decimal import Decimal
    from pathlib import Path

    import marimo as mo
    import piecash
    import polars as pl

    from gnucash_storage import (
        BookConfig,
        NewTransaction,
        get_account,
        import_transactions,
        open_configured_book,
    )

    return (
        BookConfig,
        Decimal,
        NewTransaction,
        Path,
        asdict,
        dataclass,
        get_account,
        import_transactions,
        mo,
        open_configured_book,
        piecash,
        pl,
        tempfile,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Generic statement mapping

    The MCP server does not parse CSV files. The `import_bank_statement` and
    `review_bank_statement` prompts ask Claude to work out each bank's layout
    (delimiter, date column and format, description column, signed amount or
    separate money-out/money-in columns, decimal separator), and to have the user
    confirm that mapping before parsing.

    This notebook plays Claude's part for two sample statements that look nothing
    like each other. It applies a confirmed `StatementMapping`, turns the rows into
    `NewTransaction`s, and runs them through `import_transactions` in a throwaway
    book to check that:

    - the signs come out right (money out is negative, money in is positive)
    - the dates and localized numbers parse correctly
    - importing the same statement twice reports every row as a duplicate
    """)
    return


@app.cell
def _(Path, dataclass, mo):
    @dataclass(frozen=True)
    class StatementMapping:
        """Column mapping Claude proposes and the user confirms for one CSV layout.

        Attributes:
            separator: Field delimiter.
            date_column: Booking date column.
            date_format: ``strptime`` format of ``date_column``.
            description_column: Transaction description column.
            currency: Currency of the bank account the statement belongs to.
            amount_column: Single signed amount column, if the bank uses one.
            money_out_column: Unsigned money-out column (Debit, Withdrawal, Soll).
            money_in_column: Unsigned money-in column (Credit, Deposit, Haben).
            decimal_comma: Numbers look like ``1.234,56`` instead of ``1,234.56``.
        """

        separator: str
        date_column: str
        date_format: str
        description_column: str
        currency: str
        amount_column: str | None = None
        money_out_column: str | None = None
        money_in_column: str | None = None
        decimal_comma: bool = False

    SAMPLES_DIR: Path = mo.notebook_dir() / "samples"
    MAPPINGS = {
        "us_signed_amount.csv": StatementMapping(
            separator=",",
            date_column="Transaction Date",
            date_format="%m/%d/%Y",
            description_column="Description",
            currency="USD",
            amount_column="Amount",
        ),
        "eu_debit_credit.csv": StatementMapping(
            separator=";",
            date_column="Buchungstag",
            date_format="%d.%m.%Y",
            description_column="Verwendungszweck",
            currency="EUR",
            money_out_column="Soll",
            money_in_column="Haben",
            decimal_comma=True,
        ),
    }
    return MAPPINGS, SAMPLES_DIR, StatementMapping


@app.cell
def _(MAPPINGS, mo):
    sample_picker = mo.ui.dropdown(
        options=list(MAPPINGS), value=next(iter(MAPPINGS)), label="Sample statement"
    )
    sample_picker
    return (sample_picker,)


@app.cell
def _(MAPPINGS, SAMPLES_DIR, asdict, mo, sample_picker):
    sample_path = SAMPLES_DIR / sample_picker.value
    mapping = MAPPINGS[sample_picker.value]
    mo.hstack(
        [
            mo.vstack([mo.md("**Raw CSV**"), mo.plain_text(sample_path.read_text())]),
            mo.vstack(
                [
                    mo.md("**Confirmed mapping**"),
                    mo.tree(
                        {k: v for k, v in asdict(mapping).items() if v is not None}
                    ),
                ]
            ),
        ],
        widths=[3, 2],
    )
    return mapping, sample_path


@app.cell
def _(Path, StatementMapping, pl):
    def parse_amount(column: str, decimal_comma: bool) -> pl.Expr:
        """Turn a localized number string such as ``1.234,56`` into a Decimal.

        Args:
            column: Column holding the number as text.
            decimal_comma: The decimal separator is a comma.

        Returns:
            A Decimal expression with two decimal places.
        """
        thousands, decimal = (".", ",") if decimal_comma else (",", ".")
        return (
            pl.col(column)
            .str.replace_all(thousands, "", literal=True)
            .str.replace(decimal, ".", literal=True)
            .cast(pl.Decimal(scale=2))
        )

    def parse_statement(path: Path, mapping: StatementMapping) -> pl.DataFrame:
        """Parse a statement into signed rows from the bank account's viewpoint.

        Args:
            path: CSV file.
            mapping: Confirmed column mapping.

        Returns:
            Columns ``post_date``, ``description`` and ``amount``; money out is
            negative. Rows without any amount are dropped.
        """
        raw = pl.read_csv(path, separator=mapping.separator, infer_schema=False)
        if mapping.amount_column is not None:
            has_amount = pl.col(mapping.amount_column).is_not_null()
            amount = parse_amount(mapping.amount_column, mapping.decimal_comma)
        else:
            money_in = pl.col(mapping.money_in_column)
            money_out = pl.col(mapping.money_out_column)
            has_amount = money_in.is_not_null() | money_out.is_not_null()
            amount = parse_amount(
                mapping.money_in_column, mapping.decimal_comma
            ).fill_null(0) - parse_amount(
                mapping.money_out_column, mapping.decimal_comma
            ).fill_null(0)
        return raw.filter(has_amount).select(
            pl.col(mapping.date_column)
            .str.strptime(pl.Date, mapping.date_format)
            .alias("post_date"),
            pl.col(mapping.description_column).alias("description"),
            amount.alias("amount"),
        )

    return (parse_statement,)


@app.cell
def _(mapping, parse_statement, sample_path):
    parsed = parse_statement(sample_path, mapping)
    parsed.head(10)
    return (parsed,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Import into a throwaway book

    Each run creates a fresh SQLite book in the statement's currency, with
    `Assets:Checking Account` as the bank account. Negative rows go to
    `Expenses:Miscellaneous` and positive rows to `Income:Other Income`. Picking a
    better category is Claude's job and isn't exercised here.
    """)
    return


@app.cell
def _(BookConfig, Path, mapping, piecash, tempfile):
    BANK_ACCOUNT = "Assets:Checking Account"
    EXPENSE_ACCOUNT = "Expenses:Miscellaneous"
    INCOME_ACCOUNT = "Income:Other Income"

    def create_demo_book(directory: Path, currency: str) -> BookConfig:
        """Create a small book with a bank, an expense and an income account.

        Args:
            directory: Folder for the SQLite file.
            currency: Book currency mnemonic, e.g. ``EUR``.

        Returns:
            Config for the new book, with backups off.
        """
        book_path = directory / f"demo-{currency}.gnucash"
        with piecash.create_book(
            sqlite_file=str(book_path), currency=currency, overwrite=True
        ) as book:
            commodity = book.default_currency
            for fullname, account_type in (
                ("Assets", "ASSET"),
                (BANK_ACCOUNT, "BANK"),
                ("Expenses", "EXPENSE"),
                (EXPENSE_ACCOUNT, "EXPENSE"),
                ("Income", "INCOME"),
                (INCOME_ACCOUNT, "INCOME"),
            ):
                parent_name, _, name = fullname.rpartition(":")
                parent = (
                    book.accounts(fullname=parent_name)
                    if parent_name
                    else book.root_account
                )
                piecash.Account(name, account_type, commodity, parent=parent)
                book.flush()
            book.save()
        return BookConfig(
            book_path=book_path, do_backup=False, bank_account=BANK_ACCOUNT
        )

    demo_config = create_demo_book(Path(tempfile.mkdtemp()), mapping.currency)
    return BANK_ACCOUNT, EXPENSE_ACCOUNT, INCOME_ACCOUNT, demo_config


@app.cell
def _(
    BANK_ACCOUNT,
    EXPENSE_ACCOUNT,
    INCOME_ACCOUNT,
    NewTransaction,
    demo_config,
    import_transactions,
    open_configured_book,
    parsed,
    pl,
):
    rows = [
        NewTransaction(
            post_date=record["post_date"],
            description=record["description"],
            account=BANK_ACCOUNT,
            counter_account=INCOME_ACCOUNT if record["amount"] > 0 else EXPENSE_ACCOUNT,
            amount=record["amount"],
        )
        for record in parsed.to_dicts()
    ]

    def run_import(dry_run: bool) -> list[str]:
        """Import ``rows`` into the demo book and return each row's status."""
        with open_configured_book(demo_config, readonly=dry_run) as book:
            return [
                result.status for result in import_transactions(book, rows, dry_run)
            ]

    import_report = parsed.select("post_date", "description", "amount").with_columns(
        pl.Series("preview", run_import(dry_run=True)),
        pl.Series("write", run_import(dry_run=False)),
        pl.Series("re_import", run_import(dry_run=True)),
    )
    import_report.head(10)
    return import_report, rows


@app.cell
def _(
    BANK_ACCOUNT,
    Decimal,
    demo_config,
    get_account,
    import_report,
    mo,
    open_configured_book,
    rows,
):
    with open_configured_book(demo_config, readonly=True) as _book:
        bank_balance = get_account(_book, BANK_ACCOUNT).balance
    expected_balance = sum((row.amount for row in rows), Decimal(0))

    checks = {
        "every row is new in the preview": all(
            status == "new" for status in import_report["preview"]
        ),
        "every row is added on write": all(
            status == "added" for status in import_report["write"]
        ),
        "every row is a duplicate on re-import": all(
            status == "duplicate" for status in import_report["re_import"]
        ),
        f"bank balance {bank_balance} equals the sum of amounts {expected_balance}": (
            bank_balance == expected_balance
        ),
    }
    mo.callout(
        mo.md(
            "\n".join(
                f"- {'PASS' if ok else 'FAIL'}: {name}" for name, ok in checks.items()
            )
        ),
        kind="success" if all(checks.values()) else "danger",
    )
    return


if __name__ == "__main__":
    app.run()
