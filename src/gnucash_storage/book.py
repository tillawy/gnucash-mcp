"""Read and idempotent-write operations on a GnuCash book via piecash."""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import Literal

import piecash
from piecash import Account, Split, Transaction
from piecash.core.account import ACCOUNT_TYPES

from gnucash_storage.config import BookConfig

type DuplicateKey = tuple[date, str, Decimal]
type ReconcileState = Literal["n", "c", "y"]

SQLITE_HEADER = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"
SAVE_AS_SQLITE = (
    "piecash only reads GnuCash SQL books. In GnuCash, use File > Save As and "
    "choose the sqlite3 data format, then point book_path at the new file."
)


@dataclass(frozen=True)
class AccountInfo:
    """Summary of an account.

    Attributes:
        fullname: Colon-separated account path, e.g. ``Assets:Current Assets``.
        type: GnuCash account type, e.g. ``BANK`` or ``EXPENSE``.
        commodity: Mnemonic of the account commodity, e.g. ``USD``.
        balance: Balance including children, sign-adjusted for the account type.
    """

    fullname: str
    type: str
    commodity: str
    balance: Decimal


@dataclass(frozen=True)
class SplitInfo:
    """One split of a transaction.

    Attributes:
        account: Full name of the split's account.
        value: Split value in the transaction currency.
        quantity: Split quantity in the account's own commodity, e.g. coins for a
            gold account; equal to ``value`` when the account is in the
            transaction currency.
    """

    account: str
    value: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class TransactionInfo:
    """Summary of a transaction.

    Attributes:
        guid: GnuCash transaction GUID.
        post_date: Posting date.
        description: Transaction description.
        num: Transaction number; holds the external reference ID of imported rows.
        notes: Transaction notes; holds the raw bank text of imported rows.
        splits: All splits of the transaction.
    """

    guid: str
    post_date: date
    description: str
    num: str
    notes: str | None
    splits: list[SplitInfo]


@dataclass(frozen=True)
class NewTransaction:
    """A balanced two-split transaction to add.

    Attributes:
        post_date: Posting date.
        description: Transaction description, e.g. a cleaned-up merchant name.
        account: Full name of the primary account (e.g. the bank account).
        counter_account: Full name of the balancing account.
        amount: Signed value on ``account`` (positive is money in); the counter
            split gets ``-amount``.
        external_id: Unique reference from the bank (card RRN, OFX FITID, bank
            reference), stored in the transaction's ``num`` field and used for
            duplicate detection when present.
        notes: Raw bank text for the row, stored in the transaction's notes. When
            present, duplicate detection compares it instead of ``description``.
        quantity: Units of the counter account's commodity, as a positive number;
            required when that commodity is not the bank account's currency (e.g.
            5 gold coins bought for ``amount`` 3330), and rejected otherwise. The
            counter split gets this quantity with the sign of ``-amount``.
    """

    post_date: date
    description: str
    account: str
    counter_account: str
    amount: Decimal
    external_id: str | None = None
    notes: str | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class TransactionUpdate:
    """Changes to apply to an existing transaction; ``None`` leaves a field alone.

    Attributes:
        post_date: New posting date.
        description: New description.
        external_id: New transaction number; an empty string clears it.
        notes: New notes; an empty string clears them.
        counter_account: Full name of the new balancing account.
        amount: New signed value on the bank-side account (positive is money
            in); the counter split gets ``-amount``.
        quantity: New units of the counter account's commodity, as a positive
            number; see ``NewTransaction.quantity``. When only the amount changes
            on a transaction with a commodity counter account, the existing
            units are kept.
    """

    post_date: date | None = None
    description: str | None = None
    external_id: str | None = None
    notes: str | None = None
    counter_account: str | None = None
    amount: Decimal | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one row of a batch import.

    Attributes:
        row: Zero-based index of the row in the input list.
        status: ``new`` (dry run, would be added), ``added``, or ``duplicate``.
        duplicates: Existing transactions the row matches.
        duplicate_of_row: Index of an earlier identical row in the same batch.
    """

    row: int
    status: Literal["new", "added", "duplicate"]
    duplicates: list[TransactionInfo]
    duplicate_of_row: int | None


@dataclass(frozen=True)
class ReconcileSelection:
    """Which transactions of an account to reconcile: given GUIDs or a date range.

    Pass either ``guids`` or at least one of ``start`` and ``end``, not both.

    Attributes:
        guids: GUIDs of the transactions to select.
        start: Earliest post date to select, inclusive.
        end: Latest post date to select, inclusive.
    """

    guids: list[str] | None = None
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of a reconcile run on one account.

    Attributes:
        matched: Splits of the account that the selection covers.
        changed: Matched splits whose state differs from the requested one.
        already: Matched splits that already have the requested state.
        oldest: Earliest post date among the changed splits, if any.
        newest: Latest post date among the changed splits, if any.
        written: Whether the changes were saved; false for a dry run.
    """

    matched: int
    changed: int
    already: int
    oldest: date | None
    newest: date | None
    written: bool


def _check_sqlite_book(path: Path) -> None:
    """Reject files that are not SQLite databases, with a hint for XML books.

    GnuCash saves XML (usually gzip-compressed) by default, which piecash
    cannot open; without this check piecash reports that the database does not
    exist.

    Args:
        path: Book file to inspect.

    Raises:
        ValueError: If the file is a GnuCash XML book or not a SQLite database.
        FileNotFoundError: If the file does not exist.
    """
    with path.open("rb") as book_file:
        header = book_file.read(len(SQLITE_HEADER))
    if header == SQLITE_HEADER:
        return
    if header.startswith(GZIP_MAGIC) or header.lstrip().startswith(b"<?xml"):
        raise ValueError(f"{path} is a GnuCash XML book. {SAVE_AS_SQLITE}")
    raise ValueError(f"{path} is not a SQLite GnuCash book. {SAVE_AS_SQLITE}")


@contextmanager
def open_configured_book(config: BookConfig, readonly: bool) -> Iterator[piecash.Book]:
    """Open the configured book and close it on exit.

    Read-only opens ignore the GnuCash lock, since reading is safe while
    GnuCash has the file open.

    Args:
        config: Book configuration.
        readonly: Open without write access.

    Yields:
        The open piecash book.

    Raises:
        ValueError: If the file is a GnuCash XML book or not a SQLite database.
        piecash.GnucashException: If the book is locked and ``open_if_lock`` is
            false for a writable open.
    """
    _check_sqlite_book(config.book_path)
    book = piecash.open_book(
        str(config.book_path),
        readonly=readonly,
        open_if_lock=readonly or config.open_if_lock,
        do_backup=config.do_backup,
    )
    try:
        yield book
    finally:
        book.close()


def _account_info(account: Account) -> AccountInfo:
    """Convert a piecash account to an ``AccountInfo``."""
    return AccountInfo(
        fullname=account.fullname,
        type=account.type,
        commodity=account.commodity.mnemonic,
        balance=Decimal(account.get_balance()),
    )


def list_accounts(book: piecash.Book) -> list[AccountInfo]:
    """List all accounts in the book.

    Args:
        book: Open piecash book.

    Returns:
        One entry per account, excluding the root account.
    """
    return [_account_info(account) for account in book.accounts]


def get_account(book: piecash.Book, fullname: str) -> AccountInfo:
    """Look up one account by its full name.

    Args:
        book: Open piecash book.
        fullname: Colon-separated account path.

    Returns:
        The account summary including its balance.

    Raises:
        KeyError: If no account has this full name.
    """
    return _account_info(book.accounts(fullname=fullname))


def create_account(
    book: piecash.Book,
    fullname: str,
    account_type: str,
    currency: str | None = None,
    description: str = "",
    placeholder: bool = False,
) -> AccountInfo:
    """Create an account under an existing parent.

    Args:
        book: Book opened with ``readonly=False``.
        fullname: Full name of the new account, e.g. ``Expenses:Dining``. All
            segments but the last must already exist; a single segment creates a
            top-level account.
        account_type: GnuCash account type, e.g. ``BANK`` or ``EXPENSE``.
        currency: ISO currency mnemonic; defaults to the parent's commodity, or
            the book's default currency for a top-level account.
        description: Optional account description.
        placeholder: Mark the account as a placeholder that holds no transactions.

    Returns:
        The created account.

    Raises:
        KeyError: If the parent account or the currency does not exist.
        ValueError: If the account exists, the name is empty, or the type is
            unknown.
    """
    parent_name, _, name = fullname.rpartition(":")
    if not name.strip():
        raise ValueError(f"invalid account name {fullname!r}")
    if account_type not in ACCOUNT_TYPES - {"ROOT"}:
        raise ValueError(
            f"unknown account type {account_type!r}; "
            f"use one of {sorted(ACCOUNT_TYPES - {'ROOT'})}"
        )
    if any(account.fullname == fullname for account in book.accounts):
        raise ValueError(f"account {fullname} already exists")
    parent = book.accounts(fullname=parent_name) if parent_name else book.root_account
    if currency is not None:
        commodity = book.currencies(mnemonic=currency)
    elif parent_name:
        commodity = parent.commodity
    else:
        commodity = book.default_currency
    account = Account(
        name=name,
        type=account_type,
        commodity=commodity,
        parent=parent,
        description=description,
        placeholder=int(placeholder),
    )
    book.save()
    return _account_info(account)


def _transaction_info(transaction: Transaction) -> TransactionInfo:
    """Convert a piecash transaction to a ``TransactionInfo``."""
    return TransactionInfo(
        guid=transaction.guid,
        post_date=transaction.post_date,
        description=transaction.description,
        num=transaction.num,
        notes=transaction.notes,
        splits=[
            SplitInfo(
                account=split.account.fullname,
                value=split.value,
                quantity=split.quantity,
            )
            for split in transaction.splits
        ],
    )


def list_transactions(
    book: piecash.Book,
    fullname: str,
    start: date | None,
    end: date | None,
    limit: int,
) -> list[TransactionInfo]:
    """List transactions touching an account, newest first.

    Args:
        book: Open piecash book.
        fullname: Colon-separated account path.
        start: Earliest post date to include, or ``None`` for no bound.
        end: Latest post date to include, or ``None`` for no bound.
        limit: Maximum number of transactions to return.

    Returns:
        Matching transactions ordered by post date descending.

    Raises:
        KeyError: If no account has this full name.
    """
    account = book.accounts(fullname=fullname)
    query = (
        book.query(Transaction)
        .join(Split, Transaction.guid == Split.transaction_guid)
        .filter(Split.account_guid == account.guid)
    )
    if start is not None:
        query = query.filter(Transaction.post_date >= start)
    if end is not None:
        query = query.filter(Transaction.post_date <= end)
    transactions = query.order_by(Transaction.post_date.desc()).limit(limit).all()
    return [_transaction_info(transaction) for transaction in transactions]


def find_transactions(
    book: piecash.Book, search: str, limit: int
) -> list[TransactionInfo]:
    """Find transactions whose description, notes or number contain a word.

    Scans newest first and stops after ``limit`` matches. Notes are stored as
    GnuCash slots, so the match runs in Python rather than in SQL.

    Args:
        book: Open piecash book.
        search: Text to look for, case-insensitive.
        limit: Maximum number of transactions to return.

    Returns:
        Matching transactions, newest first.
    """
    needle = search.casefold()
    newest_first = book.query(Transaction).order_by(Transaction.post_date.desc())
    matches = (
        transaction
        for transaction in newest_first
        if needle
        in f"{transaction.description}\n{transaction.notes}\n{transaction.num}".casefold()
    )
    return [_transaction_info(t) for t in islice(matches, limit)]


@dataclass(frozen=True)
class _ExistingTransactions:
    """An account's existing transactions, indexed for duplicate lookups.

    Attributes:
        by_key: Transactions keyed by (post date, bank text, split quantity on
            the account). The bank text is the notes, or the description when a
            transaction has no notes. The quantity is in the account's own
            commodity, so the key matches the statement amount whatever the
            transaction's currency.
        by_external_id: Transactions keyed by their ``num`` field.
    """

    by_key: dict[DuplicateKey, list[Transaction]]
    by_external_id: dict[str, list[Transaction]]


def _natural_key(row: NewTransaction) -> DuplicateKey:
    """Duplicate key of a row: (post date, bank text, amount).

    The bank text is the raw ``notes`` when given, else the description. Cleaned
    descriptions can vary between imports, while the bank's own text does not,
    and transactions imported before notes were used hold it in the description.
    """
    return (row.post_date, row.notes or row.description, row.amount)


def _index_existing(
    book: piecash.Book, account: Account, rows: list[NewTransaction]
) -> _ExistingTransactions:
    """Index the account's transactions that could duplicate ``rows``.

    Uses one query for the rows' date range and, when any row has an external
    ID, one more for those IDs at any date, so many rows cost two round trips.

    Args:
        book: Open piecash book.
        account: Bank-side account shared by ``rows``.
        rows: Non-empty list of rows to check.

    Returns:
        The account's candidate transactions, indexed both ways.
    """
    on_account = (
        book.query(Transaction)
        .join(Split, Transaction.guid == Split.transaction_guid)
        .filter(Split.account_guid == account.guid)
    )
    dates = [row.post_date for row in rows]
    in_date_range = (
        on_account.filter(Transaction.post_date >= min(dates))
        .filter(Transaction.post_date < max(dates) + timedelta(days=1))
        .all()
    )
    by_key: dict[DuplicateKey, list[Transaction]] = defaultdict(list)
    for transaction in in_date_range:
        for split in transaction.splits:
            if split.account == account:
                key = (
                    transaction.post_date,
                    transaction.notes or transaction.description,
                    split.quantity,
                )
                by_key[key].append(transaction)

    external_ids = {row.external_id for row in rows if row.external_id}
    by_external_id: dict[str, list[Transaction]] = defaultdict(list)
    if external_ids:
        for transaction in on_account.filter(Transaction.num.in_(external_ids)).all():
            by_external_id[transaction.num].append(transaction)
    return _ExistingTransactions(by_key=by_key, by_external_id=by_external_id)


def _matching_transactions(
    existing: _ExistingTransactions, row: NewTransaction
) -> list[Transaction]:
    """Existing transactions that ``row`` duplicates.

    A transaction matches when it carries the row's external ID, whatever its
    date or description. It also matches on (date, bank text, amount), see
    ``_natural_key``, unless
    both sides have external IDs and they differ, so two identical same-day
    purchases with different references are not duplicates, while transactions
    imported before IDs were used still are.

    Args:
        existing: Index built by ``_index_existing`` for the row's account.
        row: Row to check.

    Returns:
        Matching transactions without repeats, ID matches first.
    """
    by_id = existing.by_external_id.get(row.external_id, []) if row.external_id else []
    by_key = [
        transaction
        for transaction in existing.by_key.get(_natural_key(row), [])
        if not (row.external_id and transaction.num)
        or transaction.num == row.external_id
    ]
    return list(dict.fromkeys(by_id + by_key))


def find_duplicates(book: piecash.Book, new: NewTransaction) -> list[TransactionInfo]:
    """Find existing transactions that ``new`` would duplicate.

    See ``_matching_transactions`` for the matching rules.

    Args:
        book: Open piecash book.
        new: Candidate transaction; ``counter_account`` is not used.

    Returns:
        The matching transactions, empty if there are none.

    Raises:
        KeyError: If ``new.account`` does not exist.
    """
    account = book.accounts(fullname=new.account)
    existing = _index_existing(book, account, [new])
    return [
        _transaction_info(transaction)
        for transaction in _matching_transactions(existing, new)
    ]


def _counter_quantity(
    account: Account, counter: Account, amount: Decimal, quantity: Decimal | None
) -> Decimal:
    """Validate a row and return the quantity of its counter split.

    The transaction currency is the bank account's commodity, so a EUR account in
    a USD book gets EUR transactions. The counter account either shares that
    currency, or holds a non-currency commodity (stock, gold, ...), in which case
    ``quantity`` gives the units bought or sold for ``amount``. Converting between
    currencies is not supported.

    Args:
        account: Bank-side account; its commodity becomes the transaction currency.
        counter: Balancing account.
        amount: Signed amount on ``account``.
        quantity: Positive units of the counter commodity, or ``None`` when both
            accounts share the currency.

    Returns:
        The counter split's quantity: ``-amount`` for a same-currency counter,
        else ``quantity`` carrying the sign of ``-amount``.

    Raises:
        ValueError: If ``account`` is not held in a currency, the counter commodity
            differs and is a currency or has no ``quantity``, ``quantity`` is given
            for a same-currency counter, is not positive or goes with a zero
            ``amount``, or ``amount`` or ``quantity`` would be rounded when stored
            (e.g. ``21.345`` in a two-decimal currency).
    """
    currency = account.commodity
    if currency.namespace != "CURRENCY":
        raise ValueError(
            f"{account.fullname} is held in {currency.mnemonic}, not a currency"
        )
    amount_units = {currency.fraction, account.commodity_scu}
    counter_commodity = counter.commodity
    if counter_commodity == currency:
        amount_units.add(counter.commodity_scu)
        counter_quantity = -amount
        if quantity is not None:
            raise ValueError(
                f"quantity only applies when {counter.fullname} holds a different "
                f"commodity than {account.fullname}, which is in {currency.mnemonic}"
            )
    else:
        is_currency = counter_commodity.namespace == "CURRENCY"
        if is_currency or quantity is None:
            hint = (
                ""
                if is_currency
                else f" or pass 'quantity' in {counter_commodity.mnemonic}"
            )
            raise ValueError(
                f"{account.fullname} is in {currency.mnemonic} but "
                f"{counter.fullname} is in {counter_commodity.mnemonic}; currency "
                f"conversion is not supported, use a {currency.mnemonic} "
                f"account{hint}"
            )
        if quantity <= 0 or amount == 0:
            raise ValueError(
                "quantity must be positive and go with a non-zero amount, got "
                f"quantity {quantity} for amount {amount}"
            )
        if _has_excess_decimals(
            quantity, counter_commodity.fraction, counter.commodity_scu
        ):
            raise ValueError(
                f"quantity {quantity} has more decimals than "
                f"{counter_commodity.mnemonic} allows"
            )
        counter_quantity = quantity if amount < 0 else -quantity
    if _has_excess_decimals(amount, *amount_units):
        raise ValueError(
            f"amount {amount} has more decimals than {currency.mnemonic} allows"
        )
    return counter_quantity


def _has_excess_decimals(number: Decimal, *smallest_units: int) -> bool:
    """Whether ``number`` is not a whole multiple of 1/unit for every unit given."""
    return any((number * units) % 1 for units in smallest_units)


def _build_transaction(
    new: NewTransaction,
    account: Account,
    counter: Account,
    counter_quantity: Decimal,
) -> Transaction:
    """Build a balanced two-split transaction in the bank account's currency."""
    return Transaction(
        post_date=new.post_date,
        description=new.description,
        num=new.external_id or "",
        notes=new.notes,
        currency=account.commodity,
        splits=[
            Split(account=account, value=new.amount),
            Split(account=counter, value=-new.amount, quantity=counter_quantity),
        ],
    )


def add_transaction(
    book: piecash.Book, new: NewTransaction, force: bool = False
) -> bool:
    """Add a balanced two-split transaction unless it already exists.

    Args:
        book: Book opened with ``readonly=False``.
        new: The transaction to add.
        force: If True, insert the transaction without duplicate checking.

    Returns:
        True if the transaction was added, False if it was a duplicate and
        ``force`` was False.

    Raises:
        KeyError: If either account does not exist.
        ValueError: If the accounts' currencies differ, the counter commodity needs
            a ``quantity`` that is missing or invalid, or the amount is finer than
            the currency allows.
    """
    account = book.accounts(fullname=new.account)
    counter_account = book.accounts(fullname=new.counter_account)
    counter_quantity = _counter_quantity(
        account, counter_account, new.amount, new.quantity
    )
    if not force and _matching_transactions(_index_existing(book, account, [new]), new):
        return False
    book.add(_build_transaction(new, account, counter_account, counter_quantity))
    book.save()
    return True


def import_transactions(
    book: piecash.Book, rows: list[NewTransaction], dry_run: bool
) -> list[ImportResult]:
    """Add many transactions at once, skipping duplicates.

    Every row is validated before anything is written, so a bad account name,
    mixed currencies or an over-precise amount or quantity leave the book untouched.
    Duplicate detection (see ``_matching_transactions``) costs at most two
    queries per bank-side account. A row with the same external ID, or without
    IDs the same date, description and amount, as an earlier row in the same
    batch is also reported as a duplicate, so the caller can decide whether to
    force it.

    Example:
        >>> results = import_transactions(book, rows, dry_run=True)
        >>> [result.status for result in results]
        ['new', 'duplicate']

    Args:
        book: Open piecash book; must be writable unless ``dry_run`` is set.
        rows: Transactions to add.
        dry_run: Only report what would happen, without writing.

    Returns:
        One result per row, in input order.

    Raises:
        KeyError: If any account does not exist.
        ValueError: If a row's accounts use different currencies, its counter
            commodity needs a ``quantity`` that is missing or invalid, or its
            amount is finer than the currency allows.
    """
    accounts = {
        name: book.accounts(fullname=name)
        for name in {row.account for row in rows}
        | {row.counter_account for row in rows}
    }
    counter_quantities = [
        _counter_quantity(
            accounts[row.account],
            accounts[row.counter_account],
            row.amount,
            row.quantity,
        )
        for row in rows
    ]

    existing = {
        name: _index_existing(
            book, accounts[name], [row for row in rows if row.account == name]
        )
        for name in {row.account for row in rows}
    }

    first_row_by_key: dict[tuple[str, str | DuplicateKey], int] = {}
    results: list[ImportResult] = []
    for row_index, row in enumerate(rows):
        matches = _matching_transactions(existing[row.account], row)
        batch_key = (row.account, row.external_id or _natural_key(row))
        earlier_row = first_row_by_key.setdefault(batch_key, row_index)
        if matches or earlier_row != row_index:
            results.append(
                ImportResult(
                    row=row_index,
                    status="duplicate",
                    duplicates=[_transaction_info(match) for match in matches],
                    duplicate_of_row=None if matches else earlier_row,
                )
            )
            continue
        if not dry_run:
            book.add(
                _build_transaction(
                    row,
                    accounts[row.account],
                    accounts[row.counter_account],
                    counter_quantities[row_index],
                )
            )
        results.append(
            ImportResult(
                row=row_index,
                status="new" if dry_run else "added",
                duplicates=[],
                duplicate_of_row=None,
            )
        )
    if not dry_run and any(result.status == "added" for result in results):
        book.save()
    return results


def _get_transaction(book: piecash.Book, guid: str) -> Transaction:
    """Look up a transaction by GUID.

    Raises:
        KeyError: If no transaction has this GUID.
    """
    transaction = book.query(Transaction).filter(Transaction.guid == guid).one_or_none()
    if transaction is None:
        raise KeyError(f"no transaction with guid {guid}")
    return transaction


def update_transaction(
    book: piecash.Book,
    guid: str,
    update: TransactionUpdate,
    account: str | None = None,
) -> TransactionInfo:
    """Change fields of an existing transaction, keeping it balanced.

    Changing ``counter_account``, ``amount`` or ``quantity`` requires a two-split
    transaction and ``account``, the bank-side split that ``amount`` refers to. Everything
    is validated before anything changes, so a failure leaves the book as it was.

    Args:
        book: Book opened with ``readonly=False``.
        guid: GUID of the transaction, as returned by ``list_transactions``.
        update: The fields to change.
        account: Full name of the bank-side account; needed for ``amount``,
            ``quantity`` or ``counter_account`` changes.

    Returns:
        The transaction after the update.

    Raises:
        KeyError: If the transaction or an account does not exist.
        ValueError: If no ``account`` is given for an amount, quantity or counter
            change, the transaction does not have exactly two splits with one on
            ``account``, the currencies differ, the counter commodity needs a
            ``quantity`` that is missing or invalid, or the amount is finer than
            the currency allows.
    """
    transaction = _get_transaction(book, guid)
    changes_splits = (
        update.amount is not None
        or update.quantity is not None
        or update.counter_account is not None
    )
    if changes_splits:
        if account is None:
            raise ValueError(
                "pass 'account' to change the amount, quantity or counter account"
            )
        bank_account = book.accounts(fullname=account)
        bank_splits = [s for s in transaction.splits if s.account == bank_account]
        if len(transaction.splits) != 2 or len(bank_splits) != 1:
            raise ValueError(
                f"can only change amount, quantity or counter account of a "
                f"two-split transaction with one split on {account}"
            )
        bank_split = bank_splits[0]
        counter_split = next(s for s in transaction.splits if s is not bank_split)
        counter = (
            book.accounts(fullname=update.counter_account)
            if update.counter_account is not None
            else counter_split.account
        )
        amount = update.amount if update.amount is not None else bank_split.value
        quantity = update.quantity
        if (
            quantity is None
            and update.counter_account is None
            and counter.commodity != bank_account.commodity
        ):
            quantity = abs(counter_split.quantity)
        counter_quantity = _counter_quantity(bank_account, counter, amount, quantity)
        counter_split.account = counter
        bank_split.value = bank_split.quantity = amount
        counter_split.value = -amount
        counter_split.quantity = counter_quantity

    if update.post_date is not None:
        transaction.post_date = update.post_date
    if update.description is not None:
        transaction.description = update.description
    if update.external_id is not None:
        transaction.num = update.external_id
    if update.notes is not None:
        transaction.notes = update.notes
    book.save()
    return _transaction_info(transaction)


def clone_transaction(
    book: piecash.Book,
    guid: str,
    post_date: date,
    external_id: str | None = None,
    notes: str | None = None,
) -> TransactionInfo:
    """Add a copy of an existing transaction, with any number of splits.

    The copy keeps the original's description, currency and every split (account,
    value, quantity and memo). The date, number and notes are the new ones given;
    they are not copied, as they identify the original bank row. No duplicate
    check is made.

    Args:
        book: Book opened with ``readonly=False``.
        guid: GUID of the transaction to copy.
        post_date: Posting date of the copy.
        external_id: Transaction number of the copy; none if omitted.
        notes: Notes of the copy; none if omitted.

    Returns:
        The new transaction.

    Raises:
        KeyError: If no transaction has this GUID.
    """
    original = _get_transaction(book, guid)
    clone = Transaction(
        post_date=post_date,
        description=original.description,
        num=external_id or "",
        notes=notes,
        currency=original.currency,
        splits=[
            Split(
                account=split.account,
                value=split.value,
                quantity=split.quantity,
                memo=split.memo,
            )
            for split in original.splits
        ],
    )
    book.add(clone)
    book.save()
    return _transaction_info(clone)


def delete_transaction(book: piecash.Book, guid: str) -> TransactionInfo:
    """Delete a transaction together with its splits.

    Args:
        book: Book opened with ``readonly=False``.
        guid: GUID of the transaction, as returned by ``list_transactions``.

    Returns:
        The transaction as it was before it was deleted.

    Raises:
        KeyError: If no transaction has this GUID.
    """
    transaction = _get_transaction(book, guid)
    deleted = _transaction_info(transaction)
    book.delete(transaction)
    book.save()
    return deleted


def reconcile_transactions(
    book: piecash.Book,
    fullname: str,
    selection: ReconcileSelection,
    state: ReconcileState,
    dry_run: bool,
) -> ReconcileResult:
    """Set the reconcile state of an account's splits in the selected transactions.

    Only the split on ``fullname`` changes; the other splits of each transaction
    keep their state. Reconciled splits (``y``) get the current time as reconcile
    date, and the other states clear it. All changes are written in one save.

    Args:
        book: Book opened with ``readonly=False`` unless ``dry_run`` is true.
        fullname: Colon-separated path of the account whose splits to change.
        selection: The transactions to select, by GUID or by post date range.
        state: ``n`` for not reconciled, ``c`` for cleared, ``y`` for reconciled.
        dry_run: Only count what would change, without writing.

    Returns:
        How many splits matched, would change or already have the state.

    Raises:
        KeyError: If the account does not exist, or a GUID is not a transaction
            with a split on the account; nothing is written.
        ValueError: If the selection has both GUIDs and dates, or neither.
    """
    has_dates = selection.start is not None or selection.end is not None
    if (selection.guids is not None) == has_dates:
        raise ValueError("pass either 'guids' or a start/end date range, not both")
    account = book.accounts(fullname=fullname)
    query = (
        book.query(Split)
        .join(Transaction, Split.transaction_guid == Transaction.guid)
        .filter(Split.account_guid == account.guid)
    )
    if selection.guids is not None:
        query = query.filter(Transaction.guid.in_(selection.guids))
    if selection.start is not None:
        query = query.filter(Transaction.post_date >= selection.start)
    if selection.end is not None:
        query = query.filter(Transaction.post_date <= selection.end)
    splits = query.all()
    if selection.guids is not None:
        missing = set(selection.guids) - {split.transaction_guid for split in splits}
        if missing:
            raise KeyError(f"no transaction on {fullname} with guid {sorted(missing)}")

    to_change = [split for split in splits if split.reconcile_state != state]
    if not dry_run and to_change:
        reconcile_date = (
            datetime.now(UTC).replace(microsecond=0) if state == "y" else None
        )
        for split in to_change:
            split.reconcile_state = state
            split.reconcile_date = reconcile_date
        book.save()
    changed_dates = [split.transaction.post_date for split in to_change]
    return ReconcileResult(
        matched=len(splits),
        changed=len(to_change),
        already=len(splits) - len(to_change),
        oldest=min(changed_dates, default=None),
        newest=max(changed_dates, default=None),
        written=not dry_run,
    )
