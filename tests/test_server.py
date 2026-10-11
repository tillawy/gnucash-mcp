"""Tests for the MCP tool functions in gnucash_mcp.server."""

from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gnucash_mcp import server
from gnucash_storage import (
    BookConfig,
    NewTransaction,
    add_transaction,
    open_configured_book,
)


@pytest.fixture
def configured_server(
    book_config: BookConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point GNUCASH_MCP_CONFIG at the fixture book with one transaction in it."""
    with open_configured_book(book_config, readonly=False) as book:
        add_transaction(
            book,
            NewTransaction(
                post_date=date(2025, 11, 28),
                description="TEST BISTRO REST.",
                account="Assets:Bank",
                counter_account="Expenses:Food",
                amount=Decimal("-36.00"),
            ),
        )
    config_path = tmp_path / "book.toml"
    config_path.write_text(
        f'book_path = "{book_config.book_path.name}"\n'
        'bank_account = "Assets:Bank"\ndo_backup = false\n'
    )
    monkeypatch.setenv(server.CONFIG_ENV_VAR, str(config_path))
    server.book_config.cache_clear()
    yield
    server.book_config.cache_clear()


@pytest.mark.usefixtures("configured_server")
def test_tools_read_configured_book() -> None:
    accounts = server.list_accounts()
    bank = server.get_account("Assets:Bank")
    transactions = server.list_transactions("Assets:Bank")

    assert len(accounts) == 4
    assert bank.balance == Decimal("-36.00")
    assert [t.description for t in transactions] == ["TEST BISTRO REST."]


@pytest.mark.usefixtures("configured_server")
def test_create_account_tool() -> None:
    created = server.create_account("Expenses:Dining", "EXPENSE")

    assert created.fullname == "Expenses:Dining"
    assert server.get_account("Expenses:Dining").type == "EXPENSE"


@pytest.mark.usefixtures("configured_server")
def test_add_transaction_tool() -> None:
    # Adding a new transaction succeeds and updates account balances
    added = server.add_transaction(
        post_date=date(2025, 11, 29),
        description="Coffee & Bakery",
        account="Assets:Bank",
        counter_account="Expenses:Food",
        amount=Decimal("-4.50"),
    )
    assert added is True

    # Account balance reflects the new split
    bank = server.get_account("Assets:Bank")
    assert bank.balance == Decimal("-40.50")

    food = server.get_account("Expenses:Food")
    assert food.balance == Decimal("40.50")

    # Transactions list shows the newly inserted transaction first
    transactions = server.list_transactions("Assets:Bank")
    assert [t.description for t in transactions] == [
        "Coffee & Bakery",
        "TEST BISTRO REST.",
    ]

    # Re-adding the identical transaction is rejected idempotently
    duplicate = server.add_transaction(
        post_date=date(2025, 11, 29),
        description="Coffee & Bakery",
        account="Assets:Bank",
        counter_account="Expenses:Food",
        amount=Decimal("-4.50"),
    )
    assert duplicate is False

    # Force adding the duplicate inserts it successfully
    forced = server.add_transaction(
        post_date=date(2025, 11, 29),
        description="Coffee & Bakery",
        account="Assets:Bank",
        counter_account="Expenses:Food",
        amount=Decimal("-4.50"),
        force=True,
    )
    assert forced is True
    assert server.get_account("Assets:Bank").balance == Decimal("-45.00")


@pytest.mark.usefixtures("configured_server")
def test_add_transaction_tool_invalid_account_raises() -> None:
    with pytest.raises(KeyError):
        server.add_transaction(
            post_date=date(2025, 11, 29),
            description="Unknown",
            account="Assets:NonExistent",
            counter_account="Expenses:Food",
            amount=Decimal("-10.00"),
        )


DINNER_ROW = server.ImportRow(
    post_date=date(2025, 11, 29),
    description="ZALATIMO SWEETS",
    counter_account="Expenses:Food",
    amount=Decimal("-21.34"),
)


@pytest.mark.usefixtures("configured_server")
def test_import_transactions_tool_defaults_to_dry_run_and_bank_account() -> None:
    existing_row = server.ImportRow(
        post_date=date(2025, 11, 28),
        description="TEST BISTRO REST.",
        counter_account="Expenses:Food",
        amount=Decimal("-36.00"),
    )

    preview = server.import_transactions([existing_row, DINNER_ROW])

    assert [result.status for result in preview] == ["duplicate", "new"]
    assert server.get_account("Assets:Bank").balance == Decimal("-36.00")


@pytest.mark.usefixtures("configured_server")
def test_import_transactions_tool_writes_new_rows() -> None:
    results = server.import_transactions([DINNER_ROW], dry_run=False)

    assert [result.status for result in results] == ["added"]
    assert server.get_account("Assets:Bank").balance == Decimal("-57.34")


@pytest.mark.usefixtures("configured_server")
def test_add_transaction_tool_defaults_to_bank_account() -> None:
    added = server.add_transaction(
        post_date=date(2025, 11, 29),
        description="Coffee",
        counter_account="Expenses:Food",
        amount=Decimal("-4.50"),
    )

    assert added is True
    assert server.get_account("Assets:Bank").balance == Decimal("-40.50")


@pytest.mark.usefixtures("configured_server")
def test_tools_pass_external_id_through() -> None:
    card_row = server.ImportRow(
        post_date=date(2025, 11, 30),
        description="SQ *BLUE BOTTLE COFFEE",
        counter_account="Expenses:Food",
        amount=Decimal("-4.75"),
        external_id="606006000101",
    )
    renamed = server.ImportRow(
        post_date=date(2025, 12, 1),
        description="BLUE BOTTLE COFFEE",
        counter_account="Expenses:Food",
        amount=Decimal("-4.75"),
        external_id="606006000101",
    )

    added = server.import_transactions([card_row], dry_run=False)
    preview = server.import_transactions([renamed])
    skipped = server.add_transaction(
        post_date=renamed.post_date,
        description=renamed.description,
        counter_account=renamed.counter_account,
        amount=renamed.amount,
        external_id=renamed.external_id,
    )

    assert [result.status for result in added] == ["added"]
    assert [result.status for result in preview] == ["duplicate"]
    assert skipped is False
    listed = server.list_transactions("Assets:Bank", start_date=date(2025, 11, 30))
    assert [t.num for t in listed] == ["606006000101"]


def test_setup_steps_include_statement_notes_in_mapping_step(
    book_config: BookConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "notes.toml"
    config_path.write_text(
        f'book_path = "{book_config.book_path.name}"\n'
        "statement_notes = '''\n"
        'Details holds "REF <12 digits>"; use it as external_id.\n'
        "Card date inside Details is YY/MM/DD.\n"
        "'''\n"
    )
    monkeypatch.setenv(server.CONFIG_ENV_VAR, str(config_path))
    server.book_config.cache_clear()

    setup_steps = server._statement_setup_steps()
    server.book_config.cache_clear()

    mapping_step = setup_steps[setup_steps.index("2. ") : setup_steps.index("3. ")]
    assert "Notes about this bank's statements" in mapping_step
    assert (
        '     Details holds "REF <12 digits>"; use it as external_id.' in mapping_step
    )
    assert "     Card date inside Details is YY/MM/DD." in mapping_step
    assert "still show me the mapping to confirm" in mapping_step


@pytest.mark.usefixtures("configured_server")
def test_tools_store_notes_and_match_on_them() -> None:
    raw_text = "REF 60621600010 Zalatimo Sweets Shmeisani VISA ELECTRON PURCHASE"
    row = server.ImportRow(
        post_date=date(2025, 12, 26),
        description="Zalatimo Sweets",
        counter_account="Expenses:Food",
        amount=Decimal("-21.34"),
        notes=raw_text,
    )

    added = server.import_transactions([row], dry_run=False)
    skipped = server.add_transaction(
        post_date=row.post_date,
        description="Zalatimo",
        counter_account=row.counter_account,
        amount=row.amount,
        notes=raw_text,
    )

    assert [result.status for result in added] == ["added"]
    assert skipped is False
    (listed,) = server.list_transactions("Assets:Bank", start_date=row.post_date)
    assert (listed.description, listed.notes) == ("Zalatimo Sweets", raw_text)


@pytest.mark.parametrize(
    ("date_source", "expected"),
    [
        ("posting", "use the booking (posting) date"),
        ("transaction", "use the date the purchase happened"),
    ],
)
def test_setup_steps_follow_configured_date_source(
    book_config: BookConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    date_source: str,
    expected: str,
) -> None:
    config_path = tmp_path / "dates.toml"
    config_path.write_text(
        f'book_path = "{book_config.book_path.name}"\ndate_source = "{date_source}"\n'
    )
    monkeypatch.setenv(server.CONFIG_ENV_VAR, str(config_path))
    server.book_config.cache_clear()

    setup_steps = server._statement_setup_steps()
    server.book_config.cache_clear()

    mapping_step = setup_steps[setup_steps.index("2. ") : setup_steps.index("3. ")]
    assert expected in mapping_step
    assert "which to use" not in mapping_step


def test_resolve_account_without_config_bank_account_raises(
    book_config: BookConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "no_bank.toml"
    config_path.write_text(f'book_path = "{book_config.book_path.name}"\n')
    monkeypatch.setenv(server.CONFIG_ENV_VAR, str(config_path))
    server.book_config.cache_clear()

    with pytest.raises(ValueError, match="bank_account"):
        server.resolve_account(None)

    server.book_config.cache_clear()


def test_import_bank_statement_prompt_names_bank_account(
    configured_server: None,
) -> None:
    assert "Assets:Bank" in server.import_bank_statement()


@pytest.mark.usefixtures("configured_server")
def test_import_bank_statement_prompt_shows_num_column() -> None:
    assert "Num (the external_id" in server.import_bank_statement()


@pytest.mark.usefixtures("configured_server")
@pytest.mark.parametrize(
    "prompt", [server.import_bank_statement, server.review_bank_statement]
)
def test_statement_prompts_offer_creating_missing_accounts(
    prompt: Callable[[], str],
) -> None:
    assert "create_account" in prompt()


@pytest.mark.usefixtures("configured_server")
def test_import_bank_statement_prompt_mentions_cheque_numbers() -> None:
    assert "cheque number" in server.import_bank_statement()


@pytest.mark.usefixtures("configured_server")
def test_statement_prompts_share_setup_steps() -> None:
    setup_steps = server._statement_setup_steps()

    assert "get_account('Assets:Bank')" in setup_steps
    assert setup_steps in server.import_bank_statement()
    assert setup_steps in server.review_bank_statement()


@pytest.mark.usefixtures("configured_server")
def test_setup_steps_infer_and_confirm_csv_mapping_for_any_bank() -> None:
    setup_steps = server._statement_setup_steps()

    for concept in ("delimiter", "date column", "signed amount", "thousands"):
        assert concept in setup_steps
    assert "wait for me to confirm or correct it" in setup_steps
    assert "same currency" in setup_steps
    assert "external_id" in setup_steps
    assert "Notes about this bank" not in setup_steps
    assert "set notes to the original description text" in setup_steps
    assert "ask me\n     which to use" in setup_steps
    # No single bank's column names are baked in.
    assert "Details" not in setup_steps
    assert "dd/mm/yyyy" not in setup_steps


@pytest.mark.usefixtures("configured_server")
def test_review_bank_statement_prompt_reviews_one_row_at_a_time() -> None:
    prompt = server.review_bank_statement()

    assert "Row i/N" in prompt
    assert "Num (the external_id" in prompt
    assert "import_transactions([row], dry_run=False)" in prompt
    assert "force=True" in prompt
    assert "Clone an earlier entry" in prompt
    assert "find_transactions(search)" in prompt


def test_missing_config_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(server.CONFIG_ENV_VAR, raising=False)
    server.book_config.cache_clear()

    with pytest.raises(RuntimeError, match=server.CONFIG_ENV_VAR):
        server.book_config()


@pytest.mark.usefixtures("configured_server")
def test_update_transaction_tool_defaults_to_bank_account() -> None:
    guid = server.list_transactions("Assets:Bank")[0].guid

    updated = server.update_transaction(guid, amount=Decimal("-12.00"))

    assert {s.account: s.value for s in updated.splits} == {
        "Assets:Bank": Decimal("-12.00"),
        "Expenses:Food": Decimal("12.00"),
    }
    assert server.get_account("Assets:Bank").balance == Decimal("-12.00")


@pytest.mark.usefixtures("configured_server")
def test_update_transaction_tool_description_needs_no_account() -> None:
    guid = server.list_transactions("Assets:Bank")[0].guid

    updated = server.update_transaction(guid, description="Renamed")

    assert updated.description == "Renamed"


@pytest.mark.usefixtures("configured_server")
def test_delete_transaction_tool() -> None:
    guid = server.list_transactions("Assets:Bank")[0].guid

    deleted = server.delete_transaction(guid)

    assert deleted.guid == guid
    assert server.list_transactions("Assets:Bank") == []


@pytest.mark.usefixtures("configured_server")
def test_find_transactions_tool() -> None:
    (found,) = server.find_transactions("bistro")

    assert found.description == "TEST BISTRO REST."
    assert server.find_transactions("nomatch") == []


@pytest.mark.usefixtures("configured_server")
def test_clone_transaction_prompt_asks_for_search_word() -> None:
    prompt = server.clone_transaction()

    assert "search word" in prompt
    assert "find_transactions(search)" in prompt
    assert "add_transaction" in prompt


@pytest.mark.usefixtures("configured_server")
def test_copy_transaction_tool() -> None:
    guid = server.find_transactions("bistro")[0].guid

    copied = server.copy_transaction(guid, date(2025, 12, 1), external_id="X-1")

    assert copied.guid != guid
    assert server.get_account("Assets:Bank").balance == Decimal("-72.00")


@pytest.fixture
def commodity_server(
    commodity_config: BookConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point GNUCASH_MCP_CONFIG at the book with a JOD bank and a gold account."""
    config_path = tmp_path / "commodity.toml"
    config_path.write_text(
        f'book_path = "{commodity_config.book_path.name}"\n'
        'bank_account = "Assets:JOD Bank"\ndo_backup = false\n'
    )
    monkeypatch.setenv(server.CONFIG_ENV_VAR, str(config_path))
    server.book_config.cache_clear()
    yield
    server.book_config.cache_clear()


def buy_gold_row(quantity: Decimal | None = Decimal(5)) -> server.ImportRow:
    """A row buying gold coins for 3330 JOD, 5 of them unless told otherwise."""
    return server.ImportRow(
        post_date=date(2026, 9, 29),
        description="Buy 5 from Anis",
        counter_account="Assets:Gold",
        amount=Decimal(-3330),
        quantity=quantity,
    )


@pytest.mark.usefixtures("commodity_server")
def test_add_transaction_tool_buys_commodity_with_quantity() -> None:
    added = server.add_transaction(
        post_date=date(2026, 9, 29),
        description="Buy 5 from Anis",
        counter_account="Assets:Gold",
        amount=Decimal(-3330),
        quantity=Decimal(5),
    )

    (transaction,) = server.list_transactions("Assets:Gold")
    assert added is True
    assert {s.account: (s.value, s.quantity) for s in transaction.splits} == {
        "Assets:JOD Bank": (Decimal(-3330), Decimal(-3330)),
        "Assets:Gold": (Decimal(3330), Decimal(5)),
    }
    assert server.get_account("Assets:Gold").balance == Decimal(5)


@pytest.mark.usefixtures("commodity_server")
def test_add_transaction_tool_commodity_without_quantity_raises() -> None:
    with pytest.raises(ValueError, match="'quantity'"):
        server.add_transaction(
            post_date=date(2026, 9, 29),
            description="Buy 5 from Anis",
            counter_account="Assets:Gold",
            amount=Decimal(-3330),
        )

    assert server.list_transactions("Assets:Gold") == []


@pytest.mark.usefixtures("commodity_server")
def test_import_transactions_tool_writes_commodity_rows_once() -> None:
    first = server.import_transactions([buy_gold_row()], dry_run=False)
    second = server.import_transactions([buy_gold_row()], dry_run=False)

    assert [r.status for r in first + second] == ["added", "duplicate"]
    assert server.get_account("Assets:Gold").balance == Decimal(5)


@pytest.mark.usefixtures("commodity_server")
def test_import_transactions_tool_rejects_row_without_quantity() -> None:
    with pytest.raises(ValueError, match="'quantity'"):
        server.import_transactions([buy_gold_row(quantity=None)], dry_run=False)


@pytest.mark.usefixtures("commodity_server")
def test_update_and_copy_tools_change_amount_and_quantity_of_commodity_entry() -> None:
    server.import_transactions([buy_gold_row()], dry_run=False)
    guid = server.find_transactions("Anis")[0].guid

    copied = server.copy_transaction(guid, date(2026, 10, 5))
    updated = server.update_transaction(
        copied.guid, amount=Decimal(-3330), quantity=Decimal(4)
    )

    assert {s.account: s.quantity for s in updated.splits}["Assets:Gold"] == 4
    assert server.get_account("Assets:Gold").balance == Decimal(9)


@pytest.mark.usefixtures("configured_server")
def test_reconcile_transactions_tool_previews_then_writes() -> None:
    preview = server.reconcile_transactions(
        start_date=date(2025, 11, 1), end_date=date(2025, 11, 30)
    )
    written = server.reconcile_transactions(
        start_date=date(2025, 11, 1), end_date=date(2025, 11, 30), dry_run=False
    )
    repeated = server.reconcile_transactions(
        start_date=date(2025, 11, 1), end_date=date(2025, 11, 30), dry_run=False
    )

    assert (preview.changed, preview.written) == (1, False)
    assert (written.changed, written.written) == (1, True)
    assert (repeated.changed, repeated.already) == (0, 1)


@pytest.mark.usefixtures("configured_server")
def test_reconcile_transactions_tool_rejects_missing_selection() -> None:
    with pytest.raises(ValueError, match="either 'guids' or a start/end"):
        server.reconcile_transactions()
