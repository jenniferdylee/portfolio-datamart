"""
Run a .sql file against pl.db and print any result sets.

The DuckDB CLI is a separate executable; this only needs the Python package,
so `pip install duckdb` is the only requirement.

    python run_sql.py sql/views.sql
    python run_sql.py sql/dscr_report.sql
"""

import sys
from pathlib import Path

import duckdb

DB_PATH = "pl.db"


def render(cursor):
    """Print a result set as a plain text table."""
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    widths = [
        max([len(str(c))] + [len(str(r[i])) for r in rows]) if rows else len(str(c))
        for i, c in enumerate(cols)
    ]
    print(" | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))
    print(f"({len(rows)} rows)\n")


def is_query(statement):
    """True for statements that return rows worth printing.

    CREATE VIEW also hands back a cursor (a row count), so dispatching on the
    first real keyword keeps the output to actual result sets.
    """
    for line in statement.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return line.split(None, 1)[0].upper() in (
                "SELECT", "WITH", "FROM", "DESCRIBE", "SHOW", "PRAGMA", "EXPLAIN")
    return False


def statements(sql_path):
    """Split a .sql file into statements, dropping CLI dot-commands like '.mode box'."""
    text = "\n".join(
        line for line in Path(sql_path).read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(".")
    )
    return [s for s in text.split(";") if s.strip()]


def run_file(con, sql_path, show=True):
    """Execute every statement in sql_path on an open connection."""
    for statement in statements(sql_path):
        cursor = con.execute(statement)
        if show and is_query(statement) and cursor is not None and cursor.description:
            render(cursor)


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: python {Path(sys.argv[0]).name} <file.sql> [db]")

    sql_path = Path(sys.argv[1])
    if not sql_path.exists():
        sys.exit(f"not found: {sql_path}")

    db = sys.argv[2] if len(sys.argv) > 2 else DB_PATH
    if not Path(db).exists():
        sys.exit(f"{db} not found -- run load_pl.py first")

    con = duckdb.connect(db)
    run_file(con, sql_path)
    con.close()


if __name__ == "__main__":
    main()
