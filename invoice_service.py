import csv
import io
from fastapi import HTTPException
from db import get_conn
from models import CostRowPayload, ItemCostOverridePayload


def save_invoice_to_db(parsed: dict, source_filename: str) -> int:
    """
    把一張發票解析結果寫入 DB：
    - invoices：單頭資訊
    - invoice_items：每筆明細
    """
    conn = get_conn()
    cur = conn.cursor()

    # 先寫單頭
    cur.execute(
        """
        INSERT INTO invoices (print_date, period_start, period_end, customer_name, source_filename)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            parsed.get("print_date"),
            parsed.get("period_start"),
            parsed.get("period_end"),
            parsed.get("customer_name"),
            source_filename,
        ),
    )
    invoice_id = cur.lastrowid

    # 再寫明細
    items = parsed.get("items", [])
    for it in items:
        cur.execute(
            """
            INSERT INTO invoice_items
            (
                invoice_id,
                item_date,
                order_no,
                line_type,
                product,
                grade,
                spec,
                qty,
                measure_value,
                measure_unit,
                unit_price,
                amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                it.get("date"),
                it.get("order_no"),
                it.get("line_type", "sale"),
                it.get("product"),
                it.get("grade"),
                it.get("spec"),
                it.get("qty"),
                it.get("measure_value"),
                it.get("measure_unit"),
                it.get("unit_price"),
                it.get("amount"),
            ),
        )

    conn.commit()
    conn.close()
    return int(invoice_id)


def list_costs() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, product, grade, spec, cost_per_unit, cost_unit, effective_from
        FROM cost_table
        ORDER BY product ASC, COALESCE(grade, '') ASC, COALESCE(spec, '') ASC, id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"items": rows}


def create_cost(payload: CostRowPayload) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cost_table (product, grade, spec, cost_per_unit, cost_unit, effective_from)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload.product,
            payload.grade,
            payload.spec,
            payload.cost_per_unit,
            payload.cost_unit,
            payload.effective_from,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": int(new_id)}


def update_cost(cost_id: int, payload: CostRowPayload) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cost_table
        SET product = ?, grade = ?, spec = ?, cost_per_unit = ?, cost_unit = ?, effective_from = ?
        WHERE id = ?
        """,
        (
            payload.product,
            payload.grade,
            payload.spec,
            payload.cost_per_unit,
            payload.cost_unit,
            payload.effective_from,
            cost_id,
        ),
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    if updated == 0:
        raise HTTPException(status_code=404, detail=f"Cost row {cost_id} not found")
    return {"ok": True}


def delete_cost(cost_id: int) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM cost_table WHERE id = ?", (cost_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Cost row {cost_id} not found")
    return {"ok": True}


def import_costs_csv(content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV decode failed: {e}")

    reader = csv.DictReader(io.StringIO(text))
    required = {"product", "cost_per_unit", "cost_unit"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail="CSV header must include: product,cost_per_unit,cost_unit")

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for row in reader:
        product = (row.get("product") or "").strip()
        if not product:
            continue
        cur.execute(
            """
            INSERT INTO cost_table (product, grade, spec, cost_per_unit, cost_unit, effective_from)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                product,
                (row.get("grade") or "").strip() or None,
                (row.get("spec") or "").strip() or None,
                float(row.get("cost_per_unit") or 0),
                (row.get("cost_unit") or "").strip() or "才",
                (row.get("effective_from") or "").strip() or None,
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"ok": True, "inserted": inserted}


def set_item_cost_override(payload: ItemCostOverridePayload) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM invoice_items WHERE id = ? LIMIT 1", (payload.invoice_item_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail=f"invoice_item_id {payload.invoice_item_id} not found")

    cur.execute(
        """
        INSERT INTO item_cost_overrides (invoice_item_id, cost_per_unit, cost_unit, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(invoice_item_id) DO UPDATE SET
            cost_per_unit = excluded.cost_per_unit,
            cost_unit = excluded.cost_unit,
            updated_at = datetime('now')
        """,
        (payload.invoice_item_id, payload.cost_per_unit, payload.cost_unit),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


def list_item_cost_overrides() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            o.invoice_item_id,
            o.cost_per_unit,
            o.cost_unit,
            ii.invoice_id,
            ii.order_no,
            ii.product,
            ii.grade,
            ii.spec,
            ii.measure_value,
            ii.measure_unit,
            ii.amount
        FROM item_cost_overrides o
        JOIN invoice_items ii ON ii.id = o.invoice_item_id
        ORDER BY o.updated_at DESC, o.id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"items": rows}


def delete_item_cost_override(invoice_item_id: int) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM item_cost_overrides WHERE invoice_item_id = ?", (invoice_item_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"override for invoice_item_id={invoice_item_id} not found")
    return {"ok": True}
