"""Tests for gnucash_storage.book against a throwaway SQLite book."""

import gzip
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import piecash
import pytest
from piecash import Transaction

from gnucash_storage import (
    BookConfig,
    ImportResult,
    NewTransaction,
    ReconcileResult,
    ReconcileSelection,
    ReconcileState,
    TransactionUpdate,
    add_transaction,
    clone_transaction,
    create_account,
    delete_transaction,
    find_duplicates,
    find_transactions,
    get_account,
    import_transactions,
    list_accounts,
    list_transactions,
    open_configured_book,
    reconcile_transactions,
    update_transaction,
)

LUNCH = NewTransaction(
    post_date=date(2025, 11, 28),
    description="TEST BISTRO REST.",
    account="Assets:Bank",
    counter_account="Expenses:Food",
    amount=Decimal("-36.00"),
)


def add_all(config: BookConfig, *transactions: NewTransaction) -> list[bool]:
    """Add transactions in one writable session and return each result."""
    with open_configured_book(config, readonly=False) as book:
        return [add_transaction(book, transaction) for transaction in transactions]


def test_add_transaction_is_idempotent(book_config: BookConfig) -> None:
    results = add_all(book_config, LUNCH, LUNCH)

    with open_configured_book(book_config, readonly=True) as book:
        transaction_count = book.query(Transaction).count()

    assert results == [True, False]
    assert transaction_count == 1


def test_add_transaction_is_idempotent_across_sessions(
    book_config: BookConfig,
) -> None:
    first = add_all(book_config, LUNCH)
    second = add_all(book_config, LUNCH)

    assert (first, second) == ([True], [False])


def test_add_transaction_force_inserts_duplicates(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        first = add_transaction(book, LUNCH)
        second_skip = add_transaction(book, LUNCH, force=False)
        second_forced = add_transaction(book, LUNCH, force=True)

    with open_configured_book(book_config, readonly=True) as book:
        transaction_count = book.query(Transaction).count()

    assert (first, second_skip, second_forced) == (True, False, True)
    assert transaction_count == 2


@pytest.mark.parametrize(
    "variant",
    [
        replace(LUNCH, amount=Decimal("-37.00")),
        replace(LUNCH, post_date=date(2025, 11, 29)),
        replace(LUNCH, description="ZALATIMO SWEETS"),
        replace(LUNCH, account="Expenses:Food", counter_account="Assets:Bank"),
    ],
)
def test_add_transaction_adds_distinct_variants(
    book_config: BookConfig, variant: NewTransaction
) -> None:
    assert add_all(book_config, LUNCH, variant) == [True, True]


def test_add_transaction_balances_splits(book_config: BookConfig) -> None:
    add_all(book_config, LUNCH)

    with open_configured_book(book_config, readonly=True) as book:
        bank = get_account(book, "Assets:Bank")
        food = get_account(book, "Expenses:Food")

    assert bank.balance == Decimal("-36.00")
    assert food.balance == Decimal("36.00")


def test_add_transaction_unknown_account_raises(book_config: BookConfig) -> None:
    with pytest.raises(KeyError):
        add_all(book_config, replace(LUNCH, counter_account="Expenses:Nope"))


def test_list_accounts(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=True) as book:
        accounts = list_accounts(book)

    assert {account.fullname for account in accounts} == {
        "Assets",
        "Assets:Bank",
        "Expenses",
        "Expenses:Food",
    }
    assert {account.commodity for account in accounts} == {"USD"}


def test_get_account_unknown_raises(book_config: BookConfig) -> None:
    with (
        open_configured_book(book_config, readonly=True) as book,
        pytest.raises(KeyError),
    ):
        get_account(book, "Assets:Nope")


def test_list_transactions_filters_orders_and_limits(book_config: BookConfig) -> None:
    add_all(
        book_config,
        *(replace(LUNCH, post_date=date(2025, 11, day)) for day in range(1, 6)),
    )

    with open_configured_book(book_config, readonly=True) as book:
        bounded = list_transactions(
            book, "Assets:Bank", date(2025, 11, 2), date(2025, 11, 4), limit=50
        )
        limited = list_transactions(book, "Assets:Bank", None, None, limit=2)

    assert [t.post_date.day for t in bounded] == [4, 3, 2]
    assert [t.post_date.day for t in limited] == [5, 4]
    assert {(s.account, s.value) for s in bounded[0].splits} == {
        ("Assets:Bank", Decimal("-36.00")),
        ("Expenses:Food", Decimal("36.00")),
    }


def import_all(
    config: BookConfig, rows: list[NewTransaction], dry_run: bool
) -> list[ImportResult]:
    """Run a batch import in its own session."""
    with open_configured_book(config, readonly=dry_run) as book:
        return import_transactions(book, rows, dry_run)


def count_transactions(config: BookConfig) -> int:
    """Count all transactions in the book."""
    with open_configured_book(config, readonly=True) as book:
        return int(book.query(Transaction).count())


DINNER = replace(LUNCH, description="ZALATIMO SWEETS", amount=Decimal("-21.34"))


def test_import_dry_run_reports_new_and_writes_nothing(
    book_config: BookConfig,
) -> None:
    results = import_all(book_config, [LUNCH, DINNER], dry_run=True)

    assert [result.status for result in results] == ["new", "new"]
    assert count_transactions(book_config) == 0


def test_import_adds_new_rows_and_reports_existing_duplicates(
    book_config: BookConfig,
) -> None:
    add_all(book_config, LUNCH)

    results = import_all(book_config, [LUNCH, DINNER], dry_run=False)

    duplicate, added = results
    assert (duplicate.status, added.status) == ("duplicate", "added")
    assert [t.description for t in duplicate.duplicates] == [LUNCH.description]
    assert duplicate.duplicate_of_row is None
    assert count_transactions(book_config) == 2


def test_import_flags_repeated_row_within_batch(book_config: BookConfig) -> None:
    results = import_all(book_config, [LUNCH, DINNER, LUNCH], dry_run=False)

    assert [result.status for result in results] == ["added", "added", "duplicate"]
    assert results[2].duplicate_of_row == 0
    assert results[2].duplicates == []
    assert count_transactions(book_config) == 2


def test_import_unknown_account_writes_nothing(book_config: BookConfig) -> None:
    bad_row = replace(DINNER, counter_account="Expenses:Nope")

    with pytest.raises(KeyError):
        import_all(book_config, [LUNCH, bad_row], dry_run=False)

    assert count_transactions(book_config) == 0


@pytest.mark.parametrize("dry_run", [True, False])
def test_import_rejects_amount_finer_than_currency(
    book_config: BookConfig, dry_run: bool
) -> None:
    fine_row = replace(DINNER, amount=Decimal("-21.345"))

    with pytest.raises(ValueError, match="decimals"):
        import_all(book_config, [LUNCH, fine_row], dry_run=dry_run)

    assert count_transactions(book_config) == 0


def test_add_transaction_rejects_amount_finer_than_currency(
    book_config: BookConfig,
) -> None:
    with pytest.raises(ValueError, match="decimals"):
        add_all(book_config, replace(LUNCH, amount=Decimal("-36.005")))


def test_find_duplicates_returns_matches(book_config: BookConfig) -> None:
    add_all(book_config, LUNCH, DINNER)

    with open_configured_book(book_config, readonly=True) as book:
        matches = find_duplicates(book, LUNCH)
        misses = find_duplicates(book, replace(LUNCH, amount=Decimal("-1.00")))

    assert [t.description for t in matches] == [LUNCH.description]
    assert misses == []


def test_get_account_zero_balance_is_decimal(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=True) as book:
        balance = get_account(book, "Assets:Bank").balance

    assert isinstance(balance, Decimal)


@pytest.fixture
def multi_currency_config(book_config: BookConfig) -> BookConfig:
    """Add EUR and JOD bank and expense accounts to the USD fixture book."""
    with open_configured_book(book_config, readonly=False) as book:
        assets = book.accounts(fullname="Assets")
        expenses = book.accounts(fullname="Expenses")
        for iso in ("EUR", "JOD"):
            currency = piecash.factories.create_currency_from_ISO(iso)
            piecash.Account(f"{iso} Bank", "BANK", currency, parent=assets)
            piecash.Account(f"{iso} Food", "EXPENSE", currency, parent=expenses)
        book.save()
    return book_config


EUR_LUNCH = replace(
    LUNCH, account="Assets:EUR Bank", counter_account="Expenses:EUR Food"
)


def test_transaction_uses_bank_account_currency(
    multi_currency_config: BookConfig,
) -> None:
    add_all(multi_currency_config, EUR_LUNCH)

    with open_configured_book(multi_currency_config, readonly=True) as book:
        transaction = book.query(Transaction).one()
        currency = transaction.currency.mnemonic
        bank = get_account(book, "Assets:EUR Bank")

    assert currency == "EUR"
    assert (bank.commodity, bank.balance) == ("EUR", Decimal("-36.00"))


def test_foreign_currency_rows_are_deduplicated(
    multi_currency_config: BookConfig,
) -> None:
    results = import_all(multi_currency_config, [EUR_LUNCH, EUR_LUNCH], dry_run=False)
    again = import_all(multi_currency_config, [EUR_LUNCH], dry_run=True)

    assert [result.status for result in results] == ["added", "duplicate"]
    assert [result.status for result in again] == ["duplicate"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_import_rejects_mixed_currencies_and_writes_nothing(
    multi_currency_config: BookConfig, dry_run: bool
) -> None:
    mixed = replace(EUR_LUNCH, counter_account="Expenses:Food")

    with pytest.raises(ValueError, match="EUR.*USD"):
        import_all(multi_currency_config, [EUR_LUNCH, mixed], dry_run=dry_run)

    assert count_transactions(multi_currency_config) == 0


def test_add_transaction_rejects_mixed_currencies(
    multi_currency_config: BookConfig,
) -> None:
    with pytest.raises(ValueError, match="conversion is not supported"):
        add_all(
            multi_currency_config, replace(EUR_LUNCH, counter_account="Expenses:Food")
        )


def test_precision_follows_bank_currency_not_book_default(
    multi_currency_config: BookConfig,
) -> None:
    jod_row = replace(
        LUNCH,
        account="Assets:JOD Bank",
        counter_account="Expenses:JOD Food",
        amount=Decimal("-21.345"),
    )

    results = import_all(multi_currency_config, [jod_row], dry_run=False)

    with open_configured_book(multi_currency_config, readonly=True) as book:
        balance = get_account(book, "Assets:JOD Bank").balance
    assert [result.status for result in results] == ["added"]
    assert balance == Decimal("-21.345")


XML_BOOK = b'<?xml version="1.0" encoding="utf-8" ?>\n<gnc-v2></gnc-v2>\n'


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (XML_BOOK, "is a GnuCash XML book"),
        (gzip.compress(XML_BOOK), "is a GnuCash XML book"),
        (b"Date,Description,Amount\n", "is not a SQLite GnuCash book"),
    ],
)
@pytest.mark.parametrize("readonly", [True, False])
def test_open_rejects_non_sqlite_books_with_save_as_hint(
    tmp_path: Path, content: bytes, message: str, readonly: bool
) -> None:
    book_path = tmp_path / "book.gnucash"
    book_path.write_bytes(content)

    with (
        pytest.raises(ValueError, match=message) as error,
        open_configured_book(BookConfig(book_path=book_path), readonly=readonly),
    ):
        pass

    assert "Save As" in str(error.value)
    assert "sqlite3" in str(error.value)


RRN = "606006000101"
CARD_LUNCH = replace(LUNCH, external_id=RRN)


def statuses(results: list[ImportResult]) -> list[str]:
    """Status of each import result."""
    return [result.status for result in results]


def test_external_id_is_stored_in_transaction_num(book_config: BookConfig) -> None:
    add_all(book_config, CARD_LUNCH)

    with open_configured_book(book_config, readonly=True) as book:
        (listed,) = list_transactions(book, LUNCH.account, None, None, limit=5)

    assert listed.num == RRN


@pytest.mark.parametrize(
    "changed",
    [
        replace(CARD_LUNCH, description="TEST BISTRO RESTAURANT AMMAN"),
        replace(CARD_LUNCH, post_date=date(2025, 12, 2)),
    ],
)
def test_same_external_id_is_duplicate_despite_changed_details(
    book_config: BookConfig, changed: NewTransaction
) -> None:
    add_all(book_config, CARD_LUNCH)

    (result,) = import_all(book_config, [changed], dry_run=True)

    assert result.status == "duplicate"
    assert [t.num for t in result.duplicates] == [RRN]


def test_different_external_ids_with_same_details_are_both_added(
    book_config: BookConfig,
) -> None:
    second_coffee = replace(CARD_LUNCH, external_id="606006000199")

    results = import_all(book_config, [CARD_LUNCH, second_coffee], dry_run=False)
    again = import_all(book_config, [CARD_LUNCH, second_coffee], dry_run=True)

    assert statuses(results) == ["added", "added"]
    assert statuses(again) == ["duplicate", "duplicate"]
    assert count_transactions(book_config) == 2


def test_row_with_id_matches_older_transaction_without_id(
    book_config: BookConfig,
) -> None:
    add_all(book_config, LUNCH)

    (result,) = import_all(book_config, [CARD_LUNCH], dry_run=True)

    assert result.status == "duplicate"
    assert [t.num for t in result.duplicates] == [""]


def test_same_external_id_repeated_in_batch_is_flagged(
    book_config: BookConfig,
) -> None:
    renamed = replace(CARD_LUNCH, description="TEST BISTRO RESTAURANT")

    results = import_all(book_config, [CARD_LUNCH, renamed], dry_run=False)

    assert statuses(results) == ["added", "duplicate"]
    assert results[1].duplicate_of_row == 0
    assert count_transactions(book_config) == 1


def test_external_id_only_matches_on_the_same_account(
    book_config: BookConfig,
) -> None:
    add_all(book_config, CARD_LUNCH)
    with open_configured_book(book_config, readonly=False) as book:
        piecash.Account(
            "Savings",
            "BANK",
            book.default_currency,
            parent=book.accounts(fullname="Assets"),
        )
        book.save()
    other_statement = replace(
        CARD_LUNCH, account="Assets:Savings", description="Other statement"
    )

    (result,) = import_all(book_config, [other_statement], dry_run=True)

    assert result.status == "new"


def test_add_transaction_skips_same_external_id(book_config: BookConfig) -> None:
    renamed = replace(CARD_LUNCH, description="TEST BISTRO RESTAURANT")

    assert add_all(book_config, CARD_LUNCH, renamed) == [True, False]


RAW_TEXT = (
    "REF 606010000102 TEST BISTRO REST. 379132 REF-606010000102 "
    "25/11/25 16.12.04 VISA ELECTRON PURCHASE"
)
CLEAN_LUNCH = replace(LUNCH, description="Test Bistro Restaurant", notes=RAW_TEXT)


def test_notes_are_stored_and_listed(book_config: BookConfig) -> None:
    add_all(book_config, CLEAN_LUNCH)

    with open_configured_book(book_config, readonly=True) as book:
        (listed,) = list_transactions(book, LUNCH.account, None, None, limit=5)

    assert (listed.description, listed.notes) == ("Test Bistro Restaurant", RAW_TEXT)


def test_differently_cleaned_description_with_same_notes_is_duplicate(
    book_config: BookConfig,
) -> None:
    add_all(book_config, CLEAN_LUNCH)
    recleaned = replace(CLEAN_LUNCH, description="Test Bistro")

    (result,) = import_all(book_config, [recleaned], dry_run=True)

    assert result.status == "duplicate"
    assert [t.description for t in result.duplicates] == ["Test Bistro Restaurant"]


def test_row_with_notes_matches_older_import_holding_raw_description(
    book_config: BookConfig,
) -> None:
    add_all(book_config, replace(LUNCH, description=RAW_TEXT))

    (result,) = import_all(book_config, [CLEAN_LUNCH], dry_run=True)

    assert result.status == "duplicate"


def test_same_cleaned_description_with_different_notes_is_new(
    book_config: BookConfig,
) -> None:
    add_all(book_config, CLEAN_LUNCH)
    other_visit = replace(
        CLEAN_LUNCH, notes=RAW_TEXT.replace("606010000102", "536099999999")
    )

    (result,) = import_all(book_config, [other_visit], dry_run=True)

    assert result.status == "new"


def test_create_account_inherits_parent_currency(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        created = create_account(book, "Expenses:Dining", "EXPENSE")

    with open_configured_book(book_config, readonly=True) as book:
        names = {account.fullname for account in list_accounts(book)}

    assert (created.fullname, created.type, created.commodity) == (
        "Expenses:Dining",
        "EXPENSE",
        "USD",
    )
    assert "Expenses:Dining" in names


def test_create_account_top_level_with_currency(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        created = create_account(book, "Savings", "BANK", currency="EUR")

    assert (created.fullname, created.commodity) == ("Savings", "EUR")


@pytest.mark.parametrize(
    ("fullname", "account_type", "error"),
    [
        ("Expenses:Food", "EXPENSE", ValueError),
        ("Expenses:Dining", "NOPE", ValueError),
        ("Expenses:Dining", "ROOT", ValueError),
        ("Expenses:", "EXPENSE", ValueError),
        ("Missing:Dining", "EXPENSE", KeyError),
    ],
)
def test_create_account_rejects_invalid_input(
    book_config: BookConfig,
    fullname: str,
    account_type: str,
    error: type[Exception],
) -> None:
    with (
        open_configured_book(book_config, readonly=False) as book,
        pytest.raises(error),
    ):
        create_account(book, fullname, account_type)


def _only_guid(book: piecash.Book) -> str:
    return list_transactions(book, "Assets:Bank", None, None, 10)[0].guid


def test_update_transaction_changes_fields_amount_and_counter(
    book_config: BookConfig,
) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        create_account(book, "Expenses:Dining", "EXPENSE")
        add_transaction(book, LUNCH)
        updated = update_transaction(
            book,
            _only_guid(book),
            TransactionUpdate(
                post_date=date(2025, 12, 1),
                description="Test Bistro",
                external_id="REF1",
                notes="raw text",
                counter_account="Expenses:Dining",
                amount=Decimal("-40.50"),
            ),
            account="Assets:Bank",
        )

    with open_configured_book(book_config, readonly=True) as book:
        stored = list_transactions(book, "Assets:Bank", None, None, 10)
        assert stored == [updated]
        assert get_account(book, "Assets:Bank").balance == Decimal("-40.50")
        assert get_account(book, "Expenses:Food").balance == Decimal(0)
    assert (updated.post_date, updated.description) == (
        date(2025, 12, 1),
        "Test Bistro",
    )
    assert (updated.num, updated.notes) == ("REF1", "raw text")
    assert {(s.account, s.value) for s in updated.splits} == {
        ("Assets:Bank", Decimal("-40.50")),
        ("Expenses:Dining", Decimal("40.50")),
    }


def test_update_transaction_clears_notes_and_leaves_other_fields(
    book_config: BookConfig,
) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        add_transaction(book, replace(LUNCH, notes="raw", external_id="REF1"))
        updated = update_transaction(
            book, _only_guid(book), TransactionUpdate(notes="")
        )

    assert (updated.notes, updated.num) == ("", "REF1")
    assert updated.description == LUNCH.description


@pytest.mark.parametrize(
    ("update", "account", "error"),
    [
        (TransactionUpdate(amount=Decimal(-1)), None, ValueError),
        (TransactionUpdate(amount=Decimal("-1.005")), "Assets:Bank", ValueError),
        (TransactionUpdate(counter_account="Nope"), "Assets:Bank", KeyError),
        (TransactionUpdate(amount=Decimal(-1)), "Expenses:Dining", ValueError),
    ],
)
def test_update_transaction_invalid_leaves_book_unchanged(
    book_config: BookConfig,
    update: TransactionUpdate,
    account: str | None,
    error: type[Exception],
) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        create_account(book, "Expenses:Dining", "EXPENSE")
        add_transaction(book, LUNCH)
        before = list_transactions(book, "Assets:Bank", None, None, 10)
        with pytest.raises(error):
            update_transaction(book, _only_guid(book), update, account=account)
        assert list_transactions(book, "Assets:Bank", None, None, 10) == before


def test_update_transaction_rejects_split_changes_on_multi_split(
    book_config: BookConfig,
) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        bank, food = (
            book.accounts(fullname=n) for n in ("Assets:Bank", "Expenses:Food")
        )
        book.add(
            Transaction(
                currency=bank.commodity,
                description="split",
                post_date=date(2025, 11, 1),
                splits=[
                    piecash.Split(account=bank, value=Decimal(-3)),
                    piecash.Split(account=food, value=Decimal(1)),
                    piecash.Split(account=food, value=Decimal(2)),
                ],
            )
        )
        book.save()
        with pytest.raises(ValueError, match="two-split"):
            update_transaction(
                book,
                _only_guid(book),
                TransactionUpdate(amount=Decimal(-4)),
                account="Assets:Bank",
            )


def test_update_transaction_unknown_guid_raises(book_config: BookConfig) -> None:
    with (
        open_configured_book(book_config, readonly=False) as book,
        pytest.raises(KeyError),
    ):
        update_transaction(book, "0" * 32, TransactionUpdate(description="x"))


def test_delete_transaction_removes_it_and_its_splits(book_config: BookConfig) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        add_transaction(book, LUNCH)
        guid = _only_guid(book)
        deleted = delete_transaction(book, guid)

    with open_configured_book(book_config, readonly=True) as book:
        assert deleted.guid == guid
        assert deleted.description == LUNCH.description
        assert list_transactions(book, "Assets:Bank", None, None, 10) == []
        assert book.splits == []
        assert get_account(book, "Assets:Bank").balance == Decimal(0)


def test_delete_transaction_unknown_guid_raises(book_config: BookConfig) -> None:
    with (
        open_configured_book(book_config, readonly=False) as book,
        pytest.raises(KeyError),
    ):
        delete_transaction(book, "0" * 32)


def test_find_transactions_matches_text_newest_first(book_config: BookConfig) -> None:
    add_all(
        book_config,
        replace(LUNCH, post_date=date(2025, 11, 1)),
        replace(LUNCH, post_date=date(2025, 11, 3), description="Dinner"),
        replace(LUNCH, post_date=date(2025, 11, 2), notes="CARD lunch 42"),
    )
    needle = LUNCH.description[:4].upper()

    with open_configured_book(book_config, readonly=True) as book:
        found = find_transactions(book, needle, limit=5)
        limited = find_transactions(book, needle, limit=1)
        nothing = find_transactions(book, "zzz", limit=5)

    assert [t.post_date.day for t in found] == [2, 1]
    assert [t.post_date.day for t in limited] == [2]
    assert nothing == []


def test_clone_transaction_copies_all_splits_but_not_identity(
    book_config: BookConfig,
) -> None:
    with open_configured_book(book_config, readonly=False) as book:
        create_account(book, "Assets:Cash", "CASH", None, "", False)
        original = Transaction(
            post_date=date(2025, 11, 1),
            description="Gift",
            num="REF-1",
            notes="bank text",
            currency=book.default_currency,
            splits=[
                piecash.Split(
                    account=book.accounts(fullname="Assets:Bank"),
                    value=Decimal("-40.50"),
                ),
                piecash.Split(
                    account=book.accounts(fullname="Assets:Cash"),
                    value=Decimal("40.00"),
                ),
                piecash.Split(
                    account=book.accounts(fullname="Expenses:Food"),
                    value=Decimal("0.50"),
                ),
            ],
        )
        book.add(original)
        book.save()
        original_guid = original.guid
        cloned = clone_transaction(
            book, original_guid, date(2025, 12, 1), external_id="REF-2"
        )

    assert cloned.guid != original_guid
    assert (cloned.post_date, cloned.description) == (date(2025, 12, 1), "Gift")
    assert (cloned.num, cloned.notes) == ("REF-2", None)
    assert {(s.account, s.value) for s in cloned.splits} == {
        ("Assets:Bank", Decimal("-40.50")),
        ("Assets:Cash", Decimal("40.00")),
        ("Expenses:Food", Decimal("0.50")),
    }


def test_clone_transaction_unknown_guid_raises(book_config: BookConfig) -> None:
    with (
        open_configured_book(book_config, readonly=False) as book,
        pytest.raises(KeyError),
    ):
        clone_transaction(book, "nope", date(2025, 12, 1))


BUY_GOLD = NewTransaction(
    post_date=date(2026, 9, 29),
    description="Buy 5 from Anis",
    account="Assets:JOD Bank",
    counter_account="Assets:Gold",
    amount=Decimal(-3330),
    quantity=Decimal(5),
)


def split_amounts(config: BookConfig) -> dict[str, tuple[Decimal, Decimal]]:
    """Map each account of the only transaction to its (value, quantity)."""
    with open_configured_book(config, readonly=True) as book:
        transaction = book.query(Transaction).one()
        return {s.account.fullname: (s.value, s.quantity) for s in transaction.splits}


def test_add_transaction_buys_commodity_with_quantity(
    commodity_config: BookConfig,
) -> None:
    assert add_all(commodity_config, BUY_GOLD) == [True]

    with open_configured_book(commodity_config, readonly=True) as book:
        currency = book.query(Transaction).one().currency.mnemonic
        gold = get_account(book, "Assets:Gold")
        bank = get_account(book, "Assets:JOD Bank")
    assert currency == "JOD"
    assert split_amounts(commodity_config) == {
        "Assets:JOD Bank": (Decimal(-3330), Decimal(-3330)),
        "Assets:Gold": (Decimal(3330), Decimal(5)),
    }
    assert (gold.commodity, gold.balance) == ("XGL21-8", Decimal(5))
    assert bank.balance == Decimal(-3330)


def test_add_transaction_sells_commodity_with_negative_quantity(
    commodity_config: BookConfig,
) -> None:
    sale = replace(BUY_GOLD, amount=Decimal(700), quantity=Decimal(1))

    add_all(commodity_config, sale)

    assert split_amounts(commodity_config)["Assets:Gold"] == (
        Decimal(-700),
        Decimal(-1),
    )


def test_commodity_counter_without_quantity_is_rejected(
    commodity_config: BookConfig,
) -> None:
    with pytest.raises(ValueError, match="conversion is not supported.*'quantity'"):
        add_all(commodity_config, replace(BUY_GOLD, quantity=None))

    assert count_transactions(commodity_config) == 0


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (replace(BUY_GOLD, quantity=Decimal(0)), "quantity must be positive"),
        (replace(BUY_GOLD, quantity=Decimal(-5)), "quantity must be positive"),
        (replace(BUY_GOLD, amount=Decimal(0)), "non-zero amount"),
        (replace(BUY_GOLD, quantity=Decimal("5.5")), "quantity 5.5 has more decimals"),
        (
            replace(BUY_GOLD, amount=Decimal("-3330.0001")),
            "amount -3330.0001 has more decimals",
        ),
    ],
)
def test_commodity_counter_rejects_invalid_quantity_or_amount(
    commodity_config: BookConfig, row: NewTransaction, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        add_all(commodity_config, row)

    assert count_transactions(commodity_config) == 0


def test_quantity_is_rejected_for_same_commodity_counter(
    multi_currency_config: BookConfig,
) -> None:
    with pytest.raises(ValueError, match="quantity only applies"):
        add_all(multi_currency_config, replace(EUR_LUNCH, quantity=Decimal(36)))


def test_quantity_does_not_allow_currency_conversion(
    commodity_config: BookConfig,
) -> None:
    euro_counter = replace(BUY_GOLD, counter_account="Assets:EUR Cash")

    with pytest.raises(ValueError, match="conversion is not supported"):
        add_all(commodity_config, euro_counter)


def test_commodity_rows_are_imported_and_deduplicated(
    commodity_config: BookConfig,
) -> None:
    dry_run = import_all(commodity_config, [BUY_GOLD], dry_run=True)
    written = import_all(commodity_config, [BUY_GOLD], dry_run=False)
    again = import_all(commodity_config, [BUY_GOLD], dry_run=True)

    assert [statuses(r) for r in (dry_run, written, again)] == [
        ["new"],
        ["added"],
        ["duplicate"],
    ]
    assert split_amounts(commodity_config)["Assets:Gold"] == (
        Decimal(3330),
        Decimal(5),
    )


def test_import_with_a_bad_commodity_row_writes_nothing(
    commodity_config: BookConfig,
) -> None:
    bad = replace(BUY_GOLD, description="Other", quantity=None)

    with pytest.raises(ValueError, match="'quantity'"):
        import_all(commodity_config, [BUY_GOLD, bad], dry_run=False)

    assert count_transactions(commodity_config) == 0


def update_commodity_transaction(
    config: BookConfig, update: TransactionUpdate
) -> dict[str, tuple[Decimal, Decimal]]:
    """Apply ``update`` to the only transaction and return its split amounts."""
    with open_configured_book(config, readonly=False) as book:
        guid = list_transactions(book, "Assets:JOD Bank", None, None, 10)[0].guid
        update_transaction(book, guid, update, "Assets:JOD Bank")
    return split_amounts(config)


def test_update_amount_keeps_the_units_of_a_commodity_counter(
    commodity_config: BookConfig,
) -> None:
    add_all(commodity_config, BUY_GOLD)

    amounts = update_commodity_transaction(
        commodity_config, TransactionUpdate(amount=Decimal(-3350))
    )

    assert amounts == {
        "Assets:JOD Bank": (Decimal(-3350), Decimal(-3350)),
        "Assets:Gold": (Decimal(3350), Decimal(5)),
    }


def test_update_quantity_changes_the_units(commodity_config: BookConfig) -> None:
    add_all(commodity_config, BUY_GOLD)

    amounts = update_commodity_transaction(
        commodity_config, TransactionUpdate(quantity=Decimal(4))
    )

    assert amounts["Assets:Gold"] == (Decimal(3330), Decimal(4))


def test_update_counter_to_commodity_needs_quantity(
    commodity_config: BookConfig,
) -> None:
    add_all(
        commodity_config,
        replace(BUY_GOLD, counter_account="Assets:JOD Cash", quantity=None),
    )
    update = TransactionUpdate(counter_account="Assets:Gold")

    with pytest.raises(ValueError, match="'quantity'"):
        update_commodity_transaction(commodity_config, update)
    amounts = update_commodity_transaction(
        commodity_config, replace(update, quantity=Decimal(5))
    )

    assert amounts["Assets:Gold"] == (Decimal(3330), Decimal(5))


def reconcile_states(config: BookConfig) -> dict[str, tuple[str, bool]]:
    """Map each transaction description to its bank split's state and date set."""
    with open_configured_book(config, readonly=True) as book:
        bank = book.accounts(fullname="Assets:Bank")
        return {
            split.transaction.description: (
                split.reconcile_state,
                split.reconcile_date is not None and split.reconcile_date.year > 1970,
            )
            for split in bank.splits
        }


def add_dated_lunches(config: BookConfig) -> list[str]:
    """Add lunches on 1, 5 and 9 Dec 2025 and return their GUIDs in that order."""
    add_all(
        config,
        *(
            replace(LUNCH, post_date=date(2025, 12, day), description=f"LUNCH {day}")
            for day in (1, 5, 9)
        ),
    )
    with open_configured_book(config, readonly=True) as book:
        return [
            t.guid
            for t in sorted(book.query(Transaction).all(), key=lambda t: t.post_date)
        ]


def reconcile(
    config: BookConfig,
    selection: ReconcileSelection,
    state: ReconcileState = "y",
    dry_run: bool = False,
) -> ReconcileResult:
    """Run ``reconcile_transactions`` on ``Assets:Bank`` in its own session."""
    with open_configured_book(config, readonly=dry_run) as book:
        return reconcile_transactions(book, "Assets:Bank", selection, state, dry_run)


def test_reconcile_by_date_range_marks_only_inclusive_range(
    book_config: BookConfig,
) -> None:
    add_dated_lunches(book_config)

    result = reconcile(
        book_config, ReconcileSelection(start=date(2025, 12, 1), end=date(2025, 12, 5))
    )

    assert result == ReconcileResult(
        matched=2,
        changed=2,
        already=0,
        oldest=date(2025, 12, 1),
        newest=date(2025, 12, 5),
        written=True,
    )
    assert reconcile_states(book_config) == {
        "LUNCH 1": ("y", True),
        "LUNCH 5": ("y", True),
        "LUNCH 9": ("n", False),
    }


def test_reconcile_by_guids_changes_only_the_bank_split(
    book_config: BookConfig,
) -> None:
    guids = add_dated_lunches(book_config)

    reconcile(book_config, ReconcileSelection(guids=[guids[1]]))

    with open_configured_book(book_config, readonly=True) as book:
        transaction = book.query(Transaction).filter(Transaction.guid == guids[1]).one()
        states = {s.account.fullname: s.reconcile_state for s in transaction.splits}
    assert states == {"Assets:Bank": "y", "Expenses:Food": "n"}


def test_reconcile_dry_run_writes_nothing(book_config: BookConfig) -> None:
    add_dated_lunches(book_config)

    result = reconcile(
        book_config, ReconcileSelection(start=date(2025, 12, 1)), dry_run=True
    )

    assert (result.matched, result.changed, result.written) == (3, 3, False)
    assert {state for state, _ in reconcile_states(book_config).values()} == {"n"}


def test_reconcile_is_idempotent_and_can_be_undone(book_config: BookConfig) -> None:
    add_dated_lunches(book_config)
    everything = ReconcileSelection(start=date(2025, 1, 1))
    reconcile(book_config, everything)

    again = reconcile(book_config, everything)
    undone = reconcile(book_config, everything, state="n")

    assert (again.changed, again.already, again.oldest) == (0, 3, None)
    assert undone.changed == 3
    assert set(reconcile_states(book_config).values()) == {("n", False)}


def test_reconcile_unknown_guid_raises_and_writes_nothing(
    book_config: BookConfig,
) -> None:
    guids = add_dated_lunches(book_config)

    with pytest.raises(KeyError, match="nope"):
        reconcile(book_config, ReconcileSelection(guids=[guids[0], "nope"]))

    assert {state for state, _ in reconcile_states(book_config).values()} == {"n"}


@pytest.mark.parametrize(
    "selection",
    [
        ReconcileSelection(),
        ReconcileSelection(guids=["a"], start=date(2025, 12, 1)),
    ],
    ids=["neither", "both"],
)
def test_reconcile_needs_exactly_one_kind_of_selection(
    book_config: BookConfig, selection: ReconcileSelection
) -> None:
    with pytest.raises(ValueError, match="either 'guids' or a start/end"):
        reconcile(book_config, selection)
