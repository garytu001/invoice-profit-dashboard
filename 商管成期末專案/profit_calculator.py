from __future__ import annotations

from typing import Any


def to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def normalize_key(product: Any, grade: Any, spec: Any) -> tuple[str, str, str]:
    return (
        str(product or "").strip(),
        str(grade or "").strip(),
        str(spec or "").strip(),
    )


def build_override_map(cost_overrides: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in cost_overrides:
        key = normalize_key(item.get("product"), item.get("grade"), item.get("spec"))
        result[key] = {
            "cost_per_unit": to_float(item.get("cost_per_unit"), None),
            "cost_unit": str(item.get("cost_unit") or "").strip(),
            "source": "override",
        }
    return result


def find_cost_for_item(cur, item: dict[str, Any], override_map: dict[tuple[str, str, str], dict[str, Any]]) -> dict[str, Any] | None:
    # 1) 精確 override
    exact_key = normalize_key(item.get("product"), item.get("grade"), item.get("spec"))
    if exact_key in override_map:
        return override_map[exact_key]

    # 2) 精確成本表：product+grade+spec
    cur.execute(
        """
        SELECT cost_per_unit, cost_unit
        FROM cost_table
        WHERE product = ? AND COALESCE(grade, '') = ? AND COALESCE(spec, '') = ?
        ORDER BY COALESCE(effective_from, '') DESC, id DESC
        LIMIT 1
        """,
        exact_key,
    )
    row = cur.fetchone()
    if row:
        return {
            "cost_per_unit": to_float(row["cost_per_unit"], None),
            "cost_unit": str(row["cost_unit"] or "").strip(),
            "source": "cost_table_exact",
        }

    # 3) 退而求其次：product+grade
    product, grade, _ = exact_key
    cur.execute(
        """
        SELECT cost_per_unit, cost_unit
        FROM cost_table
        WHERE product = ? AND COALESCE(grade, '') = ? AND (spec IS NULL OR spec = '')
        ORDER BY COALESCE(effective_from, '') DESC, id DESC
        LIMIT 1
        """,
        (product, grade),
    )
    row = cur.fetchone()
    if row:
        return {
            "cost_per_unit": to_float(row["cost_per_unit"], None),
            "cost_unit": str(row["cost_unit"] or "").strip(),
            "source": "cost_table_grade_fallback",
        }

    # 4) 再退一步：product
    cur.execute(
        """
        SELECT cost_per_unit, cost_unit
        FROM cost_table
        WHERE product = ? AND (grade IS NULL OR grade = '') AND (spec IS NULL OR spec = '')
        ORDER BY COALESCE(effective_from, '') DESC, id DESC
        LIMIT 1
        """,
        (product,),
    )
    row = cur.fetchone()
    if row:
        return {
            "cost_per_unit": to_float(row["cost_per_unit"], None),
            "cost_unit": str(row["cost_unit"] or "").strip(),
            "source": "cost_table_product_fallback",
        }

    return None


def calculate_profit_for_invoice(conn, invoice_id: int, cost_overrides: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cur = conn.cursor()

    cur.execute("SELECT id FROM invoices WHERE id = ? LIMIT 1;", (invoice_id,))
    if not cur.fetchone():
        return {"error": f"Invoice {invoice_id} not found"}

    cur.execute(
        """
        SELECT
            id, item_date, order_no, line_type,
            product, grade, spec, qty, measure_value, measure_unit,
            unit_price, amount
        FROM invoice_items
        WHERE invoice_id = ?
        ORDER BY id ASC
        """,
        (invoice_id,),
    )
    rows = cur.fetchall()

    override_map = build_override_map(cost_overrides or [])

    items = []
    revenue_total = 0.0
    cogs_total = 0.0
    gp_total = 0.0
    calculable_count = 0
    missing_cost_count = 0
    unit_mismatch_count = 0

    for row in rows:
        item = dict(row)
        revenue = to_float(item.get("amount"), 0.0) or 0.0
        revenue_total += revenue

        cost_info = find_cost_for_item(cur, item, override_map)
        status = "ok"
        warnings: list[str] = []
        cogs = None
        gross_profit = None
        gross_profit_rate = None

        if not cost_info:
            status = "cost_missing"
            missing_cost_count += 1
            warnings.append("cost_missing")
        else:
            cost_per_unit = to_float(cost_info.get("cost_per_unit"), None)
            cost_unit = str(cost_info.get("cost_unit") or "").strip()
            measure_value = to_float(item.get("measure_value"), None)
            measure_unit = str(item.get("measure_unit") or "").strip()

            if cost_per_unit is None or not cost_unit:
                status = "cost_missing"
                missing_cost_count += 1
                warnings.append("cost_missing")
            elif measure_value is None or not measure_unit:
                status = "measure_missing"
                warnings.append("measure_missing")
            elif measure_unit != cost_unit:
                status = "unit_mismatch"
                unit_mismatch_count += 1
                warnings.append(f"unit_mismatch: measure={measure_unit}, cost={cost_unit}")
            else:
                signed_measure = measure_value
                if str(item.get("line_type") or "sale") == "return" and signed_measure > 0:
                    signed_measure = -signed_measure

                cogs = signed_measure * cost_per_unit
                gross_profit = revenue - cogs
                # 退貨列不提供單筆毛利率，避免負營收造成誤導。
                if str(item.get("line_type") or "sale") == "return":
                    gross_profit_rate = None
                else:
                    gross_profit_rate = (gross_profit / revenue) if revenue != 0 else None

                calculable_count += 1
                cogs_total += cogs
                gp_total += gross_profit

        items.append(
            {
                "invoice_item_id": item.get("id"),
                "date": item.get("item_date"),
                "order_no": item.get("order_no"),
                "line_type": item.get("line_type"),
                "product": item.get("product"),
                "grade": item.get("grade"),
                "spec": item.get("spec"),
                "qty": item.get("qty"),
                "measure_value": item.get("measure_value"),
                "measure_unit": item.get("measure_unit"),
                "unit_price": item.get("unit_price"),
                "amount": item.get("amount"),
                "cost_per_unit": cost_info.get("cost_per_unit") if cost_info else None,
                "cost_unit": cost_info.get("cost_unit") if cost_info else None,
                "cost_source": cost_info.get("source") if cost_info else None,
                "status": status,
                "warnings": warnings,
                "revenue": round(revenue, 2),
                "cogs": round(cogs, 2) if cogs is not None else None,
                "gross_profit": round(gross_profit, 2) if gross_profit is not None else None,
                "gross_profit_rate": round(gross_profit_rate, 6) if gross_profit_rate is not None else None,
            }
        )

    summary_revenue = round(revenue_total, 2)
    summary_cogs = round(cogs_total, 2)
    summary_gp = round(gp_total, 2)
    summary_gp_rate = (summary_gp / summary_revenue) if summary_revenue != 0 else None

    return {
        "invoice_id": invoice_id,
        "items": items,
        "summary": {
            "line_count": len(items),
            "calculable_count": calculable_count,
            "missing_cost_count": missing_cost_count,
            "unit_mismatch_count": unit_mismatch_count,
            "revenue_total": summary_revenue,
            "cogs_total": summary_cogs,
            "gross_profit_total": summary_gp,
            "gross_margin_rate": round(summary_gp_rate, 6) if summary_gp_rate is not None else None,
        },
    }
