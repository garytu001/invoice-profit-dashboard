from db import get_conn, init_db

def clear_invoice_data():
    """清空所有入庫資料，但保留成本表與費用表。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM item_cost_overrides")
    cur.execute("DELETE FROM invoice_items")
    cur.execute("DELETE FROM invoices")
    cur.execute("DELETE FROM sqlite_sequence WHERE name='invoices'")
    cur.execute("DELETE FROM sqlite_sequence WHERE name='invoice_items'")
    cur.execute("DELETE FROM sqlite_sequence WHERE name='item_cost_overrides'")
    conn.commit()
    conn.close()
    print("資料庫已清空，成本表與費用表保留！")

if __name__ == "__main__":
    print("⚠️  警告：此操作將清空所有發票與明細資料，且無法復原！")
    print("保留項目：cost_table、operating_expenses")
    ans = input("確定要繼續？請輸入 YES 確認：").strip()
    if ans == "YES":
        clear_invoice_data()
    else:
        print("已取消，資料庫未變動。")
