from db import get_conn, init_db

conn = get_conn()
cur = conn.cursor()

# 清空所有入庫資料（保留成本表）
cur.execute("DELETE FROM item_cost_overrides")
cur.execute("DELETE FROM invoice_items")
cur.execute("DELETE FROM invoices")

# 重設自動編號從 1 開始
cur.execute("DELETE FROM sqlite_sequence WHERE name='invoices'")
cur.execute("DELETE FROM sqlite_sequence WHERE name='invoice_items'")
cur.execute("DELETE FROM sqlite_sequence WHERE name='item_cost_overrides'")

conn.commit()
conn.close()
print("資料庫已清空，成本表保留！")