import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # GnuCash Data Exploration

    This notebook demonstrates how to interact with GnuCash files using the piecash library.

    ## Setup

    First, ensure you have a GnuCash file (SQLite or MySQL format) to work with.
    """)
    return


@app.cell
def _():
    from sqlite3.dbapi2 import Date
    from piecash import open_book
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=True)
    print('=== Accounts ===')
    for _account in _book.accounts:
        print(f'{_account.name} ({_account.type})')
    # Test: List all accounts
    print('\n=== Recent Transactions ===')
    all_splits = []
    for _account in _book.accounts:
        all_splits.extend(_account.splits)
    # Test: Get recent transactions (last 5)
    recent_splits = sorted(all_splits, key=lambda s: s.transaction.post_date, reverse=True)[:5]
    for split in recent_splits:
        tx = split.transaction
        print(f'{tx.post_date} - {tx.description} - {split.value}')
    # Sort by date, get last 5
    _book.close()
    return (open_book,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    The user wants to create a new transaction in a GnuCash file using the `piecash` library within a Jupyter Notebook.

    Task Summary:
    1. Re-open the GnuCash book in read-write mode (since the previous cell closed it and opened it as `readonly=True`).
    2. Identify or create two accounts (e.g., an Expense account and a Bank account) to create a balanced transaction.
    3. Create a `Transaction` object with two `Split` objects.
    4. Save the changes to the GnuCash file.

    Note: Since I don't know the exact account names in the user's specific GnuCash file, I will fetch the first two available accounts to demonstrate the process.
    """)
    return


@app.cell
def _(open_book):
    from piecash import Transaction, Split
    from datetime import datetime
    from decimal import Decimal
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=False)
    _currency = _book.default_currency
    acc_asset = _book.accounts(fullname='Assets:Current Assets:Cash in Wallet')
    print(acc_asset.guid)
    print(acc_asset.name)
    acc_expense = _book.accounts(fullname='Expenses:Hobbies')
    now = datetime.now()
    _trans = Transaction(post_date=now.date(), enter_date=now, description='Test transaction from PyCharm', currency=_currency, splits=[Split(account=acc_asset, value=Decimal('-10.00')), Split(account=acc_expense, value=Decimal('10.00'))])
    _book.add(_trans)
    _book.save()
    print(f'Transaction created: {_trans.description} between {acc_asset.name} and {acc_expense.name}')
    _book.close()
    return Decimal, Split, Transaction, datetime


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    <llm-snippet-file>gnucash_exploration.ipynb</llm-snippet-file>
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Reading & Inserting Demo Data
    The following cells read and display the contents of the `demo-data.csv` file using the `pandas` library.
    """)
    return


@app.cell
def _(Decimal, Split, Transaction, datetime, open_book):
    import pandas as pd
    from datetime import timedelta
    from datetime import date
    df = pd.read_csv(filepath_or_buffer='demo-data-1.csv', sep=',', header=0, parse_dates=['Date', 'Value Date'], encoding='utf-8', dayfirst=True)
    print(df.Debit)
    df['Debit'] = pd.to_numeric(df['Debit'], errors='coerce')
    df['Credit'] = pd.to_numeric(df['Credit'], errors='coerce')
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=False)
    _currency = _book.default_currency
    savings_asset_account = _book.accounts(fullname='Assets:Current Assets:Savings Account')
    other_account = _book.accounts(fullname='Expenses:Hobbies')

    def transaction_exists(_book, _post_date, _description, _account, _value):
        """Check if transaction already exists"""
        candidates = _book.query(Transaction).filter(Transaction.post_date >= _post_date).filter(Transaction.post_date < _post_date + timedelta(days=1)).filter(Transaction.description == _description).all()
        print(f'Checking {_description} for {_account} with value {_value}')
        print(f'Found {len(candidates)} candidates')
        for _trans in candidates:
            for _split in _trans.splits:
                if _split.account == _account and _split.value == _value:
                    return True
        return False
    for idx, row in df.iterrows():
        _description = row['Details']
        debt_value = 0
        credit_value = 0
        if pd.notna(row['Debit']):
            debt_value = Decimal(str(row['Debit']))
            credit_value = Decimal(-debt_value)
        elif pd.notna(row['Credit']):
            credit_value = Decimal(str(row['Credit']))
            debt_value = Decimal(-credit_value)
        else:
            continue
        print(f'{idx}: {row.Debit} {debt_value} :: {row.Credit} {credit_value}')
        _post_date = row['Date'].date()
        _trans = Transaction(post_date=_post_date, enter_date=datetime.now(), description=_description, currency=_currency, splits=[Split(account=savings_asset_account, value=debt_value), Split(account=other_account, value=credit_value)])
        if transaction_exists(_book, _post_date, _description, savings_asset_account, debt_value):
            print(f'SKIPPED (duplicate): {_post_date} - {_description[:50]}')
        else:
            _book.add(_trans)
            _book.save()
            print(f'Added: {_post_date} - {_description[:50]}')
    _book.close()
    return date, timedelta


@app.cell
def _(Transaction, date, open_book, timedelta):
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=False)
    _post_date = date(2026, 1, 1)
    _description = 'REF 606618000108 CINEMA CITY MALL 515390 REF-606618000108 26/03/07 21.10.55'
    _book.query(Transaction).filter(Transaction.post_date >= _post_date).filter(Transaction.post_date >= _post_date + timedelta(days=1)).all()
    return


@app.cell
def _():
    text = 'REF 606216000105 KUNAFA HOUSE 318455 REF-606216000105 26/03/03 19.52.41 VISA ELECTRON PURCHASE'
    words = [word for word in text.split() if word.isalpha()]
    import re
    _result = re.findall('\\b[A-Za-z]+\\b', text)
    result2 = list(set(_result))
    print(result2)
    return


@app.cell
def _(Split, Transaction, open_book):
    from sqlalchemy.dialects import sqlite
    from sqlalchemy import or_, case, func
    from piecash import Account

    def find_most_recent_transaction_account(_book, search_terms, _bank_account):
        filters = []
        scores = []
        for term in search_terms:
            term_lower = term.lower()
            condition = func.lower(Transaction.description).like(f'%{term_lower}%')
            filters.append(condition)
            scores.append(case([(condition, 1)], else_=0))
        relevance = sum(scores).label('relevance')
        result = _book.query(Account, Transaction, relevance).join(Split, Transaction.guid == Split.transaction_guid).join(Account, Account.guid == Split.account_guid).filter(Split.account_guid != _bank_account.guid).filter(or_(*filters)).order_by(relevance.desc(), Transaction.post_date.desc()).first()
        return result
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=True)
    _bank_account = _book.accounts(fullname='Assets:Current Assets:Savings Account')
    _result = find_most_recent_transaction_account(_book, ['bistro'], _bank_account)
    if _result:
        _account, transaction, score = _result
        print(f' • {_account.name}: {transaction.description} (score: {score})')
    _book.close()
    return


@app.cell
def _(Split, Transaction, open_book):
    _book = open_book('/path/to/project/db/mcp.gnucash', readonly=True)
    _bank_account = _book.accounts(fullname='Assets:Current Assets:Savings Account')
    print(_bank_account.guid)
    _result = _book.query(Transaction).join(Split, Transaction.guid == Split.transaction_guid).filter(Split.account_guid == _bank_account.guid).all()
    print(_result)
    _book.close()
    return


if __name__ == "__main__":
    app.run()
