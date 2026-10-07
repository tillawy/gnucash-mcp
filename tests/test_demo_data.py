"""Keep notebooks/demo-data-1.csv synthetic and internally consistent.

The demo statement mimics a Jordanian bank export: card rows carry a retrieval
reference number (RRN) built from the year's last digit, the day of year and the UTC
hour of the card transaction, followed by the card date as YY/MM/DD HH.MM.SS.
"""

import csv
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

DEMO_CSV = Path(__file__).resolve().parent.parent / "notebooks" / "demo-data-1.csv"
CARD_ROW = re.compile(
    r"^REF (?P<rrn>\d{12}) .+ REF-(?P=rrn) "
    r"(?P<card_time>\d{2}/\d{2}/\d{2} \d{2}\.\d{2}\.\d{2}) VISA ELECTRON PURCHASE$"
)
IBAN = re.compile(r"\b[A-Z]{2}(?P<check>\d{2})[A-Z0-9]{11,30}\b")
JORDAN = timezone(timedelta(hours=3))


def read_rows() -> list[dict[str, str]]:
    """Rows of the demo statement, newest first as in the file."""
    with DEMO_CSV.open(newline="") as statement:
        return list(csv.DictReader(statement))


def signed_amount(row: dict[str, str]) -> Decimal:
    """Row amount from the bank account's view: money out is negative."""
    return Decimal(row["Credit"] or "0") - Decimal(row["Debit"] or "0")


def test_every_row_has_exactly_one_amount() -> None:
    for row in read_rows():
        assert bool(row["Debit"]) != bool(row["Credit"]), row["Details"]


def test_running_balance_is_consistent() -> None:
    oldest_first = list(reversed(read_rows()))

    for previous, row in pairwise(oldest_first):
        expected = Decimal(previous["Balance"]) + signed_amount(row)
        assert Decimal(row["Balance"]) == expected, row["Details"]


@pytest.mark.parametrize(
    "row",
    [row for row in read_rows() if row["Details"].startswith("REF ")],
    ids=lambda row: row["Details"][:16],
)
def test_card_row_reference_matches_its_card_date(row: dict[str, str]) -> None:
    match = CARD_ROW.match(row["Details"])
    assert match is not None
    card_time = datetime.strptime(match["card_time"], "%y/%m/%d %H.%M.%S").replace(
        tzinfo=JORDAN
    )
    utc = card_time.astimezone(UTC)
    posted = datetime.strptime(row["Date"], "%d/%m/%Y").replace(tzinfo=JORDAN)

    rrn = match["rrn"]
    assert int(rrn[0]) == utc.year % 10
    assert int(rrn[1:4]) == utc.timetuple().tm_yday
    assert int(rrn[4:6]) == utc.hour
    assert card_time.date() <= posted.date()


def test_ibans_are_obviously_fake() -> None:
    """Real IBANs never use check digits 00, so these cannot be anyone's account."""
    ibans = IBAN.finditer(DEMO_CSV.read_text())

    assert all(iban["check"] == "00" for iban in ibans)
