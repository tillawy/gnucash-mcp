# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/tillawy/gnucash-mcp/security/advisories/new).
Do not open a public issue for a security problem. Include the version, the steps to
reproduce, and what an attacker could do. You should get a first reply within a week.

Only the latest release gets security fixes.

## What to know before you use it

This server reads and writes real financial data, so treat it accordingly.

- **It runs locally over stdio.** It opens no network port. It can reach only the
  GnuCash books named in the config file you point `GNUCASH_MCP_CONFIG` at.
- **Writes change your book.** Read tools and dry runs open the book read-only. Writing
  tools and `import_transactions` with `dry_run=False` modify it, and
  `delete_transaction` removes a transaction permanently. Keep `do_backup = true`, the
  default, so piecash writes a backup before each writable open.
- **Close GnuCash first.** `open_if_lock` is `false` by default. Setting it to `true`
  writes to a book that GnuCash desktop has open, which can corrupt it.
- **Your data goes to the model.** Account names, transactions and statement contents
  are sent to whichever Claude client you connect. Check that client's data policy
  before using a real book.
- **Statement text is untrusted input.** A crafted bank description could try to steer
  the model. Review the import preview before approving writes, and use a separate
  development book while trying things out.
- **Never commit your books.** `.gitignore` excludes `*.gnucash`, backups, statements
  and CSV files. Keep your own config files (`books/*.toml`) out of public repositories
  if they contain bank names or account paths.
