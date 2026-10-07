"""Per-book configuration loaded from a TOML file."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

type DateSource = Literal["posting", "transaction"]


@dataclass(frozen=True)
class BookConfig:
    """Settings for opening a single GnuCash book.

    Attributes:
        book_path: Absolute path to the GnuCash SQLite file.
        open_if_lock: Open for writing even if GnuCash holds the lock.
        do_backup: Let piecash write a timestamped backup before writing.
        bank_account: Full name of the default bank-side account for imports.
        statement_notes: Free-text hints about this bank's statement format,
            added to the import prompts.
        date_source: Which date becomes the transaction's post date: the
            statement's ``posting`` (booking) date, or the ``transaction`` date
            the purchase happened, e.g. a card date. ``None`` means ask during
            each import.
    """

    book_path: Path
    open_if_lock: bool = False
    do_backup: bool = True
    bank_account: str | None = None
    statement_notes: str | None = None
    date_source: DateSource | None = None


def load_config(path: Path) -> BookConfig:
    """Load a book configuration from a TOML file.

    A relative ``book_path`` is resolved against the TOML file's directory.

    Example TOML::

        book_path = "../db/mcp.gnucash"
        open_if_lock = false
        do_backup = true
        bank_account = "Assets:Current Assets:Checking Account"
        statement_notes = '''
        Details holds the merchant and a reference like "REF 606006000101".
        '''
        date_source = "posting"

    Args:
        path: Path to the TOML configuration file.

    Returns:
        The parsed configuration.

    Raises:
        ValueError: If ``book_path`` is missing or does not point to a file, or
            ``date_source`` is not ``posting`` or ``transaction``.
    """
    with path.open("rb") as config_file:
        raw = tomllib.load(config_file)
    if "book_path" not in raw:
        raise ValueError(f"{path}: missing required key 'book_path'")
    book_path = (path.parent / raw["book_path"]).resolve()
    if not book_path.is_file():
        raise ValueError(f"{path}: book file not found: {book_path}")
    date_source = raw.get("date_source")
    allowed_date_sources = get_args(DateSource.__value__)
    if date_source is not None and date_source not in allowed_date_sources:
        raise ValueError(
            f"{path}: date_source must be one of {allowed_date_sources}, "
            f"not {date_source!r}"
        )
    return BookConfig(
        book_path=book_path,
        open_if_lock=bool(raw.get("open_if_lock", False)),
        do_backup=bool(raw.get("do_backup", True)),
        bank_account=raw.get("bank_account"),
        statement_notes=raw.get("statement_notes"),
        date_source=date_source,
    )
