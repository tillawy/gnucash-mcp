"""Shared fixtures: a throwaway GnuCash book and its config."""

from pathlib import Path

import piecash
import pytest

from gnucash_storage import BookConfig


@pytest.fixture
def book_config(tmp_path: Path) -> BookConfig:
    """Create a USD book with Assets:Bank and Expenses:Food and return its config."""
    book_path = tmp_path / "test.gnucash"
    with piecash.create_book(sqlite_file=str(book_path), currency="USD") as book:
        usd = book.default_currency
        assets = piecash.Account("Assets", "ASSET", usd, parent=book.root_account)
        expenses = piecash.Account("Expenses", "EXPENSE", usd, parent=book.root_account)
        piecash.Account("Bank", "BANK", usd, parent=assets)
        piecash.Account("Food", "EXPENSE", usd, parent=expenses)
        book.save()
    return BookConfig(book_path=book_path, do_backup=False)


@pytest.fixture
def commodity_config(book_config: BookConfig) -> BookConfig:
    """Add JOD bank and cash accounts, a EUR cash account and a gold account to the fixture book.

    The gold account holds whole coins (fraction 1) in the commodity XGL21-8.
    """
    with piecash.open_book(
        str(book_config.book_path), readonly=False, do_backup=False
    ) as book:
        assets = book.accounts(fullname="Assets")
        jod = piecash.factories.create_currency_from_ISO("JOD")
        eur = piecash.factories.create_currency_from_ISO("EUR")
        gold = piecash.Commodity(
            namespace="COMMODITY", mnemonic="XGL21-8", fullname="Gold 21k", fraction=1
        )
        piecash.Account("JOD Bank", "BANK", jod, parent=assets)
        piecash.Account("JOD Cash", "CASH", jod, parent=assets)
        piecash.Account("EUR Cash", "CASH", eur, parent=assets)
        piecash.Account("Gold", "STOCK", gold, parent=assets)
        book.save()
    return book_config
