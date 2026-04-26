import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "app.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _has_column(cur, table_name: str, column_name: str) -> bool:
    cur.execute(f"PRAGMA table_info({table_name});")
    cols = [row[1] for row in cur.fetchall()]
    return column_name in cols


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        print_date TEXT,
        period_start TEXT,
        period_end TEXT,
        customer_name TEXT,
        source_filename TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        item_date TEXT,
        order_no TEXT,
        line_type TEXT DEFAULT 'sale',
        product TEXT,
        grade TEXT,
        spec TEXT,
        qty REAL,
        measure_value REAL,
        measure_unit TEXT,
        unit_price REAL,
        amount REAL,
        FOREIGN KEY(invoice_id) REFERENCES invoices(id)
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS cost_table (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product TEXT NOT NULL,
        grade TEXT,
        spec TEXT,
        cost_per_unit REAL NOT NULL,
        cost_unit TEXT DEFAULT '才',
        effective_from TEXT
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS item_cost_overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_item_id INTEGER NOT NULL UNIQUE,
        cost_per_unit REAL NOT NULL,
        cost_unit TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(invoice_item_id) REFERENCES invoice_items(id)
    );
    """
    )

    # 遷移舊資料庫欄位，避免既有 app.db 直接報錯
    if not _has_column(cur, "invoice_items", "measure_value"):
        cur.execute("ALTER TABLE invoice_items ADD COLUMN measure_value REAL;")

    if not _has_column(cur, "invoice_items", "measure_unit"):
        cur.execute("ALTER TABLE invoice_items ADD COLUMN measure_unit TEXT;")

    if not _has_column(cur, "invoice_items", "line_type"):
        cur.execute("ALTER TABLE invoice_items ADD COLUMN line_type TEXT DEFAULT 'sale';")
        cur.execute(
            """
            UPDATE invoice_items
            SET line_type = CASE
                WHEN qty < 0 OR amount < 0 THEN 'return'
                ELSE 'sale'
            END
            WHERE line_type IS NULL OR line_type = '';
            """
        )

    if _has_column(cur, "cost_table", "unit") and not _has_column(cur, "cost_table", "cost_unit"):
        cur.execute("ALTER TABLE cost_table ADD COLUMN cost_unit TEXT;")
        cur.execute("UPDATE cost_table SET cost_unit = COALESCE(unit, '才') WHERE cost_unit IS NULL;")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("DB initialized:", DB_PATH)
