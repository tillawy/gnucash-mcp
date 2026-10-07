"""Guard the storage boundary: only gnucash_storage may use piecash or SQLAlchemy.

piecash is barely maintained and pinned to SQLAlchemy 1.4. Keeping every use of it
inside ``gnucash_storage`` means the backend can be replaced without touching the
MCP server.
"""

import ast
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
BACKEND_MODULES = {"piecash", "sqlalchemy", "sqlalchemy_utils"}
STORAGE_PACKAGE = SRC_DIR / "gnucash_storage"


def imported_top_level_modules(path: Path) -> set[str]:
    """Top-level module names imported anywhere in a source file.

    Args:
        path: Python source file.

    Returns:
        Names such as ``piecash`` for ``from piecash.core import Book``.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize(
    "source_file",
    [
        path
        for path in sorted(SRC_DIR.rglob("*.py"))
        if not path.is_relative_to(STORAGE_PACKAGE)
    ],
    ids=lambda path: str(path.relative_to(SRC_DIR)),
)
def test_backend_is_only_imported_by_gnucash_storage(source_file: Path) -> None:
    assert imported_top_level_modules(source_file).isdisjoint(BACKEND_MODULES)


def test_gnucash_storage_is_where_piecash_lives() -> None:
    storage_imports = set().union(
        *(imported_top_level_modules(path) for path in STORAGE_PACKAGE.rglob("*.py"))
    )

    assert "piecash" in storage_imports
