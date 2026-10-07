"""Tests for gnucash_storage.config."""

from pathlib import Path

import pytest

from gnucash_storage import BookConfig, load_config


def test_load_config_resolves_relative_path_and_defaults(tmp_path: Path) -> None:
    (tmp_path / "db").mkdir()
    book_path = tmp_path / "db" / "book.gnucash"
    book_path.touch()
    config_dir = tmp_path / "books"
    config_dir.mkdir()
    config_path = config_dir / "book.toml"
    config_path.write_text('book_path = "../db/book.gnucash"\n')

    config = load_config(config_path)

    assert config == BookConfig(book_path=book_path.resolve())


def test_load_config_reads_flags(tmp_path: Path) -> None:
    (tmp_path / "book.gnucash").touch()
    config_path = tmp_path / "book.toml"
    config_path.write_text(
        'book_path = "book.gnucash"\nopen_if_lock = true\ndo_backup = false\n'
        'bank_account = "Assets:Bank"\n'
    )

    config = load_config(config_path)

    assert config.open_if_lock is True
    assert config.do_backup is False
    assert config.bank_account == "Assets:Bank"
    assert config.statement_notes is None
    assert config.date_source is None


@pytest.mark.parametrize("date_source", ["posting", "transaction"])
def test_load_config_reads_date_source(tmp_path: Path, date_source: str) -> None:
    (tmp_path / "book.gnucash").touch()
    config_path = tmp_path / "book.toml"
    config_path.write_text(
        f'book_path = "book.gnucash"\ndate_source = "{date_source}"\n'
    )

    assert load_config(config_path).date_source == date_source


def test_load_config_rejects_unknown_date_source(tmp_path: Path) -> None:
    (tmp_path / "book.gnucash").touch()
    config_path = tmp_path / "book.toml"
    config_path.write_text('book_path = "book.gnucash"\ndate_source = "value"\n')

    with pytest.raises(ValueError, match="date_source must be one of"):
        load_config(config_path)


def test_load_config_reads_multiline_statement_notes(tmp_path: Path) -> None:
    (tmp_path / "book.gnucash").touch()
    config_path = tmp_path / "book.toml"
    config_path.write_text(
        'book_path = "book.gnucash"\n'
        "statement_notes = '''\n"
        'Details holds "REF <12 digits>".\n'
        "Card date is YY/MM/DD.\n"
        "'''\n"
    )

    config = load_config(config_path)

    assert config.statement_notes == (
        'Details holds "REF <12 digits>".\nCard date is YY/MM/DD.\n'
    )


def test_load_config_requires_book_path(tmp_path: Path) -> None:
    config_path = tmp_path / "book.toml"
    config_path.write_text("do_backup = false\n")

    with pytest.raises(ValueError, match="book_path"):
        load_config(config_path)


def test_load_config_rejects_missing_book_file(tmp_path: Path) -> None:
    config_path = tmp_path / "book.toml"
    config_path.write_text('book_path = "missing.gnucash"\n')

    with pytest.raises(ValueError, match="not found"):
        load_config(config_path)
