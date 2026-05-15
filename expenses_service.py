"""
營業費用相關的資料庫操作
"""
from db import get_conn


def save_expense(year_month: str, category: str, amount: float, note: str = "") -> int:
    """新增一筆營業費用"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO operating_expenses (year_month, category, amount, note)
        VALUES (?, ?, ?, ?)
        """,
        (year_month, category, amount, note or ""),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(new_id)


def list_expenses(year_month: str = None) -> list[dict]:
    """列出費用，可依年月篩選"""
    conn = get_conn()
    cur = conn.cursor()
    if year_month:
        cur.execute(
            "SELECT * FROM operating_expenses WHERE year_month = ? ORDER BY category",
            (year_month,)
        )
    else:
        cur.execute("SELECT * FROM operating_expenses ORDER BY year_month DESC, category")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def delete_expense(expense_id: int) -> bool:
    """刪除一筆費用"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM operating_expenses WHERE id = ?", (expense_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_expense_summary(year_month: str = None) -> dict:
    """
    彙整費用：
    回傳各類別小計 + 總計
    """
    expenses = list_expenses(year_month)
    by_category = {}
    total = 0.0
    for e in expenses:
        cat = e["category"]
        amt = float(e["amount"])
        by_category[cat] = by_category.get(cat, 0.0) + amt
        total += amt
    return {
        "by_category": by_category,
        "total": round(total, 2),
        "items": expenses,
    }