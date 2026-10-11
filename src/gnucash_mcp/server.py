"""FastMCP server exposing GnuCash tools."""

import os
import textwrap
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from gnucash_storage import (
    AccountInfo,
    BookConfig,
    ImportResult,
    NewTransaction,
    ReconcileResult,
    ReconcileSelection,
    ReconcileState,
    TransactionInfo,
    TransactionUpdate,
    load_config,
    open_configured_book,
)
from gnucash_storage import add_transaction as storage_add_transaction
from gnucash_storage import clone_transaction as storage_clone_transaction
from gnucash_storage import create_account as storage_create_account
from gnucash_storage import delete_transaction as storage_delete_transaction
from gnucash_storage import find_transactions as storage_find_transactions
from gnucash_storage import get_account as storage_get_account
from gnucash_storage import import_transactions as storage_import_transactions
from gnucash_storage import list_accounts as storage_list_accounts
from gnucash_storage import list_transactions as storage_list_transactions
from gnucash_storage import reconcile_transactions as storage_reconcile_transactions
from gnucash_storage import update_transaction as storage_update_transaction

CONFIG_ENV_VAR = "GNUCASH_MCP_CONFIG"

# Mapping-step bullet telling Claude which date becomes post_date, keyed by the
# book config's date_source.
DATE_SOURCE_RULES: dict[str | None, str] = {
    "posting": """   - post_date: use the booking (posting) date, as the book config sets
     date_source = "posting";
""",
    "transaction": """   - post_date: use the date the purchase happened, as the book config sets
     date_source = "transaction". Take it from a transaction-date column or a
     card date inside the description, and fall back to the booking date for
     rows without one;
""",
    None: """   - post_date: if the statement has both a booking date and a separate
     purchase date (a column, or a card date inside the description), ask me
     which to use, and suggest I set date_source = "posting" or "transaction"
     in the book config so every import uses the same date. Otherwise use the
     booking date;
""",
}

mcp = FastMCP("gnucash-mcp-server", json_response=True)


@cache
def book_config() -> BookConfig:
    """Load the book configuration named by ``GNUCASH_MCP_CONFIG``.

    Returns:
        The configuration, loaded once per process.

    Raises:
        RuntimeError: If the environment variable is not set.
    """
    config_path = os.environ.get(CONFIG_ENV_VAR)
    if not config_path:
        raise RuntimeError(f"{CONFIG_ENV_VAR} must point to a book TOML config")
    return load_config(Path(config_path))


def resolve_account(account: str | None) -> str:
    """Return ``account``, or the configured ``bank_account`` when it is None.

    Raises:
        ValueError: If both are unset.
    """
    resolved = account or book_config().bank_account
    if resolved is None:
        raise ValueError("pass 'account' or set 'bank_account' in the book config")
    return resolved


@dataclass(frozen=True)
class ImportRow:
    """One bank statement row to import.

    Attributes:
        post_date: Posting date.
        description: Short, readable description, e.g. the merchant name.
        counter_account: Full name of the category account, e.g. 'Expenses:Dining'.
        amount: Signed value on the bank account (negative for money out).
        account: Bank-side account; defaults to the configured ``bank_account``.
        external_id: Unique bank reference for the row (card RRN, OFX FITID, bank
            reference), if the statement has one.
        notes: The row's original bank text, verbatim; kept in the transaction's
            notes and used for duplicate detection.
        quantity: Units of the counter account's commodity, as a positive number,
            when it is not the bank account's currency, e.g. 5 for five gold
            coins bought for ``amount`` -3330 (a price of 666 each). Required in
            that case and rejected otherwise; the direction follows ``amount``.
    """

    post_date: date
    description: str
    counter_account: str
    amount: Decimal
    account: str | None = None
    external_id: str | None = None
    notes: str | None = None
    quantity: Decimal | None = None


@mcp.tool()
def list_accounts() -> list[AccountInfo]:
    """List every account in the GnuCash book with its type and balance."""
    with open_configured_book(book_config(), readonly=True) as book:
        return storage_list_accounts(book)


@mcp.tool()
def get_account(fullname: str) -> AccountInfo:
    """Get one account by full name (e.g. 'Assets:Current Assets:Savings Account')."""
    with open_configured_book(book_config(), readonly=True) as book:
        return storage_get_account(book, fullname)


@mcp.tool()
def create_account(
    fullname: str,
    account_type: str,
    currency: str | None = None,
    description: str = "",
    placeholder: bool = False,
) -> AccountInfo:
    """Create an account, e.g. 'Expenses:Dining' of type 'EXPENSE'.

    Args:
        fullname: Full name of the new account. Its parent (all segments but the
            last) must already exist.
        account_type: GnuCash account type: ASSET, BANK, CASH, CREDIT, EQUITY,
            EXPENSE, INCOME, LIABILITY, MUTUAL, PAYABLE, RECEIVABLE, STOCK or
            TRADING.
        currency: ISO currency code such as 'EUR'; defaults to the parent's
            currency, or the book's default for a top-level account.
        description: Optional account description.
        placeholder: Mark the account as a placeholder that holds no transactions.

    Returns:
        The created account.

    Raises:
        KeyError: If the parent account or the currency does not exist.
        ValueError: If the account already exists or the type is unknown.
    """
    with open_configured_book(book_config(), readonly=False) as book:
        return storage_create_account(
            book, fullname, account_type, currency, description, placeholder
        )


@mcp.tool()
def list_transactions(
    fullname: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
) -> list[TransactionInfo]:
    """List transactions of an account, newest first, within optional date bounds."""
    with open_configured_book(book_config(), readonly=True) as book:
        return storage_list_transactions(book, fullname, start_date, end_date, limit)


@mcp.tool()
def find_transactions(search: str, limit: int = 5) -> list[TransactionInfo]:
    """Find transactions across all accounts by a word, newest first.

    Matches the word, case-insensitively, anywhere in the description, notes or
    number.

    Args:
        search: Word or phrase to look for.
        limit: Maximum number of transactions to return.

    Returns:
        Matching transactions, newest first, with all their splits.
    """
    with open_configured_book(book_config(), readonly=True) as book:
        return storage_find_transactions(book, search, limit)


@mcp.tool()
def add_transaction(
    post_date: date,
    description: str,
    counter_account: str,
    amount: Decimal,
    account: str | None = None,
    external_id: str | None = None,
    notes: str | None = None,
    force: bool = False,
    quantity: Decimal | None = None,
) -> bool:
    """Add a balanced two-split transaction unless it already exists.

    Args:
        post_date: Posting date.
        description: Transaction description.
        counter_account: Full name of the balancing account (e.g. 'Expenses:Food').
        amount: Signed value on ``account`` (positive is money in); the counter
            split gets ``-amount``.
        account: Full name of the bank-side account; defaults to the configured
            ``bank_account``.
        external_id: Unique bank reference (card RRN, OFX FITID, bank reference),
            stored in the transaction number and matched for duplicate detection.
        notes: Original bank text, verbatim; stored in the transaction notes and
            compared instead of ``description`` for duplicate detection.
        force: If True, bypass duplicate checking and force insertion.
        quantity: Units of the counter account's commodity, as a positive number,
            when it is not the bank account's currency, e.g. 5 for five gold
            coins bought for ``amount`` -3330 (a price of 666 each). Required in
            that case and rejected otherwise; the direction follows ``amount``.

    Returns:
        True if the transaction was added, False if it was a duplicate and
        ``force`` was False.

    Raises:
        KeyError: If either account does not exist.
        ValueError: If no account is given or configured, the counter account's
            commodity needs a ``quantity`` that is missing or invalid, or the
            amount is finer than the currency allows.
    """
    bank_account = resolve_account(account)
    with open_configured_book(book_config(), readonly=False) as book:
        return storage_add_transaction(
            book,
            NewTransaction(
                post_date=post_date,
                description=description,
                account=bank_account,
                counter_account=counter_account,
                amount=amount,
                external_id=external_id,
                notes=notes,
                quantity=quantity,
            ),
            force=force,
        )


@mcp.tool()
def copy_transaction(
    guid: str,
    post_date: date,
    external_id: str | None = None,
    notes: str | None = None,
) -> TransactionInfo:
    """Add a copy of an existing transaction, keeping all its splits.

    Use this to clone a transaction with more than two splits, which
    ``add_transaction`` cannot write. Find the ``guid`` with ``find_transactions``.
    The copy has the same description, accounts and amounts; only the date,
    ``external_id`` and ``notes`` are new, since the original's identify its own
    bank row. No duplicate check is made. To change the amount or quantity of the
    copy, call ``update_transaction`` on the returned ``guid``. Show me the
    transaction and the new date, and get my confirmation, before calling this.

    Args:
        guid: GUID of the transaction to copy.
        post_date: Posting date of the copy.
        external_id: Transaction number of the copy (card RRN, bank reference).
        notes: Original bank text of the copy, verbatim.

    Returns:
        The new transaction.

    Raises:
        KeyError: If no transaction has this GUID.
    """
    with open_configured_book(book_config(), readonly=False) as book:
        return storage_clone_transaction(book, guid, post_date, external_id, notes)


@mcp.tool()
def update_transaction(
    guid: str,
    post_date: date | None = None,
    description: str | None = None,
    external_id: str | None = None,
    notes: str | None = None,
    counter_account: str | None = None,
    amount: Decimal | None = None,
    account: str | None = None,
    quantity: Decimal | None = None,
) -> TransactionInfo:
    """Change an existing transaction; fields left out stay as they are.

    Find the ``guid`` with ``list_transactions``. Show me the transaction and the
    change, and get my confirmation, before calling this.

    Args:
        guid: GUID of the transaction to change.
        post_date: New posting date.
        description: New description.
        external_id: New transaction number; an empty string clears it.
        notes: New notes; an empty string clears them.
        counter_account: Full name of the new balancing account.
        amount: New signed value on ``account`` (positive is money in); the
            counter split gets ``-amount``.
        account: Full name of the bank-side account; defaults to the configured
            ``bank_account``. Only used with ``amount``, ``quantity`` or
            ``counter_account``.
        quantity: New units of the counter account's commodity, as a positive
            number, e.g. 5 coins for a gold counter account. Needed when
            ``counter_account`` changes to a commodity account; when only
            ``amount`` changes, the existing units are kept.

    Returns:
        The transaction after the update.

    Raises:
        KeyError: If the transaction or an account does not exist.
        ValueError: If no account is given or configured for an amount,
            quantity or counter account change, the transaction is not a
            two-split transaction with one split on ``account``, the currencies
            differ, the counter commodity needs a ``quantity`` that is missing
            or invalid, or the amount is finer than the currency allows.
    """
    changes_splits = (
        amount is not None or quantity is not None or counter_account is not None
    )
    bank_account = resolve_account(account) if changes_splits else None
    with open_configured_book(book_config(), readonly=False) as book:
        return storage_update_transaction(
            book,
            guid,
            TransactionUpdate(
                post_date=post_date,
                description=description,
                external_id=external_id,
                notes=notes,
                counter_account=counter_account,
                amount=amount,
                quantity=quantity,
            ),
            account=bank_account,
        )


@mcp.tool()
def delete_transaction(guid: str) -> TransactionInfo:
    """Permanently delete a transaction and its splits.

    Find the ``guid`` with ``list_transactions``. Show me the transaction and get
    my confirmation before calling this; it cannot be undone.

    Args:
        guid: GUID of the transaction to delete.

    Returns:
        The transaction as it was before it was deleted.

    Raises:
        KeyError: If no transaction has this GUID.
    """
    with open_configured_book(book_config(), readonly=False) as book:
        return storage_delete_transaction(book, guid)


@mcp.tool()
def reconcile_transactions(
    guids: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    state: ReconcileState = "y",
    account: str | None = None,
    dry_run: bool = True,
) -> ReconcileResult:
    """Set the reconcile state of an account's transactions, by GUID or date range.

    Select the transactions with ``guids`` (find them with ``list_transactions``)
    or with ``start_date`` and/or ``end_date``, not both. Only the split on
    ``account`` changes. Call with ``dry_run=True`` (the default) first, show me
    the counts and dates, and get my confirmation before calling with
    ``dry_run=False``. The write is saved in one go, so it also works when
    GnuCash's own reconcile window crashes on a large account.

    Args:
        guids: GUIDs of the transactions to change.
        start_date: Earliest post date to change, inclusive.
        end_date: Latest post date to change, inclusive.
        state: ``y`` reconciled (the default), ``c`` cleared or ``n`` not reconciled.
        account: Full name of the account whose splits change; defaults to the
            configured ``bank_account``.
        dry_run: Only report what would change, without writing.

    Returns:
        How many splits matched, changed or already had the state, the oldest
        and newest post dates among the changed ones, and whether they were saved.

    Raises:
        KeyError: If the account does not exist, or a GUID is not a transaction
            with a split on the account; nothing is written.
        ValueError: If both GUIDs and dates are given, or neither, or no account
            is given or configured; nothing is written.
    """
    bank_account = resolve_account(account)
    with open_configured_book(book_config(), readonly=dry_run) as book:
        return storage_reconcile_transactions(
            book,
            bank_account,
            ReconcileSelection(guids=guids, start=start_date, end=end_date),
            state,
            dry_run,
        )


@mcp.tool()
def import_transactions(
    rows: list[ImportRow], dry_run: bool = True
) -> list[ImportResult]:
    """Import many statement rows at once; duplicates are reported, not added.

    Call with ``dry_run=True`` (the default) first to preview which rows are new
    and which duplicate existing transactions, then with ``dry_run=False`` to
    write the new rows. Re-add a reviewed duplicate with
    ``add_transaction(..., force=True)``.

    Args:
        rows: Statement rows; ``account`` defaults to the configured bank account.
        dry_run: Only report what would happen, without writing.

    Returns:
        One result per row, in input order, with status ``new``, ``added`` or
        ``duplicate`` and the matching existing transactions.

    Raises:
        KeyError: If any account does not exist; nothing is written.
        ValueError: If an amount is finer than the currency allows, a counter
            account's commodity needs a ``quantity`` that is missing or invalid,
            or no bank account is given or configured; nothing is written.
    """
    new_transactions = [
        NewTransaction(
            post_date=row.post_date,
            description=row.description,
            account=resolve_account(row.account),
            counter_account=row.counter_account,
            amount=row.amount,
            external_id=row.external_id,
            notes=row.notes,
            quantity=row.quantity,
        )
        for row in rows
    ]
    with open_configured_book(book_config(), readonly=dry_run) as book:
        return storage_import_transactions(book, new_transactions, dry_run)


def _statement_setup_steps() -> str:
    """Setup steps shared by the statement import prompts.

    Covers currency, column mapping, parsing and category, for any bank's CSV.
    The book config's ``statement_notes``, if any, are added to the mapping step.

    Returns:
        Numbered steps 1-4, ending with a newline.
    """
    config = book_config()
    bank_account = config.bank_account or "(ask the user for the bank account)"
    notes = (
        "   Notes about this bank's statements, from the book config. Use them as\n"
        "   hints, but still show me the mapping to confirm:\n"
        f"{textwrap.indent(config.statement_notes.strip(), '     ')}\n"
        if config.statement_notes
        else ""
    )
    return f"""1. Call get_account({bank_account!r}) to find its commodity. The CSV's
   amounts are assumed to be in that currency — never infer the currency from
   decimal formatting or symbols in the CSV. State the currency to me
   explicitly (e.g. "Importing as EUR, based on {bank_account}") and wait for
   me to confirm or correct it before parsing any amounts.
2. Work out the CSV layout from its header and first rows. Headers may be in
   any language; do not assume a particular bank. Determine:
   - the delimiter (comma, semicolon, tab);
   - the booking date column and its format. Use a day above 12 to tell
     dd/mm from mm/dd; if every date is ambiguous, ask me;
{DATE_SOURCE_RULES[config.date_source]}   - the description column;
   - how amounts are given: one signed amount column, or separate money-out
     and money-in columns (e.g. Debit/Credit, Withdrawal/Deposit, Soll/Haben);
   - the decimal and thousands separators (e.g. 1,234.56 vs 1.234,56);
   - a unique transaction reference, if there is one: a reference column, or
     an ID inside the description (e.g. "REF 606006000101", or a cheque number
     in a cheque row). Keep it as text, with leading zeros. Say so if there
     is none;
   - the row order: banks often list the newest row first. Compare the first
     and last booking dates and say which order the file is in.
{notes}   Ignore balance columns. Show me the mapping as a short list plus one row
   parsed with it (including the cleaned description from step 3), and
   wait for me to confirm or correct it.
3. Parse every row with the confirmed mapping, in chronological order,
   oldest first. If the file lists the newest row first, read it from the
   bottom up: reverse the file's row order, and do not sort by date, so rows
   on the same day keep the bank's own order. The rows you pass to
   import_transactions, and "row" numbers in tables, follow this order.
   amount is signed from the bank
   account's point of view: negative for money out, positive for money in.
   Skip rows without an amount. Set description to a short, readable name,
   usually the merchant or counterparty (e.g. "Zalatimo Sweets" rather than
   the full card narrative), and set notes to the original description text
   verbatim; duplicate detection compares notes, so never shorten them. If I
   ask to keep the bank text as the description, use it for both. Set
   external_id to the row's reference when the mapping has one.
4. The bank-side account is {bank_account}. Call list_accounts once and pick a
   leaf EXPENSE or INCOME account as counter_account for each row from the
   description. It must be in the same currency (commodity) as the bank
   account, because currency conversion is not supported. Use
   Expenses:Miscellaneous when unsure. If a better category is missing (or no
   suitable account exists in that currency), do not guess and do not write to
   an account that does not exist: offer to create it with create_account,
   proposing the full name, type (EXPENSE or INCOME) and currency. Create it
   only after I confirm, creating missing parent accounts first, then use it
   as counter_account.
"""


@mcp.prompt()
def import_bank_statement() -> str:
    """Import a bank statement CSV: preview all rows as a table, then write in one go."""
    return f"""Import the bank statement CSV I provide into GnuCash.

{_statement_setup_steps()}5. Call import_transactions(rows, dry_run=True). Show me a table: row, date,
   Num (the external_id, or "(none)"), description, amount, counter_account,
   status. For duplicates, show the matching existing transaction (or the
   earlier identical row).
6. Wait for my confirmation or corrections, then call
   import_transactions(rows, dry_run=False).
7. For each duplicate, ask me whether to force or skip it. On force, call
   add_transaction with the same fields and force=True.
8. Summarize how many rows were added, forced and skipped."""


@mcp.prompt()
def review_bank_statement() -> str:
    """Import a bank statement CSV one row at a time, confirming each entry."""
    return f"""Import the bank statement CSV I provide into GnuCash, reviewing one row
at a time with me.

{_statement_setup_steps()}
5. Call import_transactions(rows, dry_run=True) once to get every row's status.
6. Present exactly one row at a time as "Row i/N": date, Num (the external_id
   stored as the transaction number, or "(none)"), description, amount with
   currency, proposed counter_account, and status. For a duplicate, show the
   matching existing transaction (or the earlier identical row).
7. Ask me what to do, as a multiple-choice question if you can:
   - Accept
   - Change category, description or amount (then show the row again); if
     the category I want does not exist, offer to create it
   - Clone an earlier entry: ask me for a search word, call
     find_transactions(search), and take the newest match (if the matches
     differ in counter account, let me pick one). Show it with all its splits.
     If it has two splits, on my confirmation copy its counter account and
     description onto this row (keep the row's date, amount, external_id and
     notes), then show the row again. If it has more splits, say so and
     offer: clone it exactly with copy_transaction(guid, post_date,
     external_id, notes) using this row's date, external_id and notes, which
     is only right if the row's amount equals the entry's bank-side split;
     otherwise offer the two-split clone, or going back. After
     copy_transaction, the row is written: do not import it again. If
     nothing matches, say so and show the options again
   - Skip
   - Force (only for duplicates)
   - Accept all remaining
   - Stop
   Wait for my answer before moving on. Never write a row unless I chose
   Accept, Force or Accept all remaining.
8. On Accept, call import_transactions([row], dry_run=False) and tell me the
   resulting status. If it is "duplicate", ask me whether to force or skip it.
   On Force, call add_transaction with the same fields and force=True.
   On Skip, write nothing.
9. On Accept all remaining, call import_transactions(remaining_new_rows,
   dry_run=False) once, then still review remaining duplicates one at a time.
10. On Stop or after the last row, summarize how many rows were added, forced,
    skipped and not reviewed."""


@mcp.prompt()
def clone_transaction() -> str:
    """Add an entry by cloning the latest earlier transaction that looks like it."""
    return """Add a GnuCash entry by cloning an earlier, similar transaction.

1. Ask me for a search word (e.g. a merchant name) and wait for my answer.
2. Call find_transactions(search). If nothing matches, tell me and ask for
   another word.
3. Take the newest match as the candidate. Show it: date, description, amount
   and counter account taken from its splits. If other matches differ in
   counter account or amount, list them briefly and ask which one to clone;
   otherwise ask me to confirm the candidate.
4. Ask me for the new date (default: today) and the new amount (default: the
   candidate's amount), and whether to change the description. Wait for my
   answer.
5. Show the entry to be added and wait for my confirmation. It reuses the
   candidate's accounts and description. If the candidate has more than two
   splits, call copy_transaction(guid, post_date) instead, which keeps every
   split and amount as they were; the amount cannot change. Do not copy its external_id or notes:
   they identify the original bank row and would mark the clone as a duplicate.
6. Call add_transaction with the confirmed fields. The amount is signed on the
   bank-side account, so use the sign of the cloned split. If the call reports
   a duplicate, show me the existing transaction and ask whether to force it.
7. Tell me what was added."""


def main() -> None:
    """Validate the configuration and run the server over stdio."""
    book_config()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
