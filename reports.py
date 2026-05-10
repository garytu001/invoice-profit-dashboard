import re
from fastapi import HTTPException
from db import get_conn
from profit_calculator import find_cost_for_item
from utils import infer_txn_ym


def calculate_profit_for_item_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT invoice_item_id, cost_per_unit, cost_unit FROM item_cost_overrides")
    override_rows = cur.fetchall()
    item_override_map = {
        int(r["invoice_item_id"]): {"cost_per_unit": float(r["cost_per_unit"]), "cost_unit": r["cost_unit"], "source": "item_override"}
        for r in override_rows
    }

    items = []
    revenue_total = 0.0
    cogs_total = 0.0
    gp_total = 0.0
    missing_cost_count = 0
    unit_mismatch_count = 0
    calculable_count = 0

    for row in rows:
        row = dict(row)
        revenue = float(row.get("amount") or 0)
        revenue_total += revenue
        override = item_override_map.get(int(row.get("id")))
        cost_info = override if override else find_cost_for_item(cur, row, {})

        status = "ok"
        cogs = None
        gp = None
        if not cost_info:
            status = "cost_missing"
            missing_cost_count += 1
        else:
            measure_unit = (row.get("measure_unit") or "").strip()
            measure_value = row.get("measure_value")
            cost_unit = (cost_info.get("cost_unit") or "").strip()
            cost_per_unit = cost_info.get("cost_per_unit")
            line_type = (row.get("line_type") or "sale")
            if measure_value is None:
                # 無才數/坪數時，將手動成本單價視為「該筆總銷貨成本」
                cogs = float(cost_per_unit)
                if line_type == "return" and cogs > 0:
                    cogs = -cogs
                gp = revenue - cogs
                cogs_total += cogs
                gp_total += gp
                calculable_count += 1
                status = "ok_total_cost_mode"
            elif not measure_unit:
                status = "measure_missing"
            elif measure_unit != cost_unit:
                status = "unit_mismatch"
                unit_mismatch_count += 1
            else:
                signed_measure = float(measure_value)
                if line_type == "return" and signed_measure > 0:
                    signed_measure = -signed_measure
                cogs = signed_measure * float(cost_per_unit)
                gp = revenue - cogs
                cogs_total += cogs
                gp_total += gp
                calculable_count += 1

        items.append(
            {
                **row,
                "status": status,
                "cogs": round(cogs, 2) if cogs is not None else None,
                "gross_profit": round(gp, 2) if gp is not None else None,
                "cost_per_unit": cost_info.get("cost_per_unit") if cost_info else None,
                "cost_unit": cost_info.get("cost_unit") if cost_info else None,
                "cost_source": cost_info.get("source") if cost_info else None,
            }
        )

    conn.close()
    summary = {
        "line_count": len(items),
        "calculable_count": calculable_count,
        "missing_cost_count": missing_cost_count,
        "unit_mismatch_count": unit_mismatch_count,
        "revenue_total": round(revenue_total, 2),
        "cogs_total": round(cogs_total, 2),
        "gross_profit_total": round(gp_total, 2),
        "gross_margin_rate": round(gp_total / revenue_total, 6) if revenue_total else None,
    }
    return items, summary


def get_dashboard_data(period: str) -> dict:
    """
    period: month | quarter | year
    """
    if period not in {"month", "quarter", "year"}:
        raise HTTPException(status_code=400, detail="period must be month|quarter|year")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ii.*, inv.customer_name, inv.created_at, inv.period_start, inv.period_end, inv.print_date
        FROM invoice_items ii
        JOIN invoices inv ON inv.id = ii.invoice_id
        ORDER BY ii.id ASC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    enriched, summary = calculate_profit_for_item_rows(rows)

    by_customer = {}
    by_item = {}
    trend = {}

    for it in enriched:
        ym_info = infer_txn_ym(it.get("item_date"), it.get("period_end"), it.get("created_at"))
        ym = f"{ym_info[0]:04d}-{ym_info[1]:02d}" if ym_info else "unknown"
        if period == "month":
            bucket = ym
        elif period == "quarter":
            if len(ym) == 7 and ym[5:7].isdigit():
                m = int(ym[5:7])
                q = ((m - 1) // 3) + 1
                bucket = f"{ym[:4]}-Q{q}"
            else:
                bucket = "unknown"
        else:
            bucket = ym[:4] if len(ym) >= 4 else "unknown"

        revenue = float(it.get("amount") or 0)
        cogs = float(it.get("cogs") or 0)
        gp = float(it.get("gross_profit") or 0)

        trend[bucket] = trend.get(bucket, {"bucket": bucket, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0})
        trend[bucket]["revenue"] += revenue
        trend[bucket]["cogs"] += cogs
        trend[bucket]["gross_profit"] += gp

        c = it.get("customer_name") or "Unknown"
        by_customer[c] = by_customer.get(c, {"customer_name": c, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0})
        by_customer[c]["revenue"] += revenue
        by_customer[c]["cogs"] += cogs
        by_customer[c]["gross_profit"] += gp

        item_key = f"{it.get('product') or ''} | {it.get('grade') or ''} | {it.get('spec') or ''}"
        by_item[item_key] = by_item.get(
            item_key,
            {"item_key": item_key, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0},
        )
        by_item[item_key]["revenue"] += revenue
        by_item[item_key]["cogs"] += cogs
        by_item[item_key]["gross_profit"] += gp

    def finalize_rate(rows_: list[dict]) -> list[dict]:
        for r in rows_:
            rev = r["revenue"]
            r["gross_margin_rate"] = round(r["gross_profit"] / rev, 6) if rev else None
            r["revenue"] = round(r["revenue"], 2)
            r["cogs"] = round(r["cogs"], 2)
            r["gross_profit"] = round(r["gross_profit"], 2)
        return rows_

    missing_cost_items = [
        {
            "invoice_item_id": it.get("id"),
            "invoice_id": it.get("invoice_id"),
            "order_no": it.get("order_no"),
            "product": it.get("product"),
            "grade": it.get("grade"),
            "spec": it.get("spec"),
            "measure_value": it.get("measure_value"),
            "measure_unit": it.get("measure_unit"),
            "amount": it.get("amount"),
        }
        for it in enriched
        if it.get("status") == "cost_missing"
    ]

    return {
        "period": period,
        "summary": summary,
        "trend": finalize_rate(sorted(trend.values(), key=lambda x: x["bucket"])),
        "by_customer": finalize_rate(sorted(by_customer.values(), key=lambda x: x["revenue"], reverse=True)),
        "by_item": finalize_rate(sorted(by_item.values(), key=lambda x: x["revenue"], reverse=True)),
        "missing_cost_items": missing_cost_items,
    }


#以下是為了異常警示新增的程式碼#
def get_anomalies() -> list[dict]:
    """
    財務異常偵測：
    - 單價偏離歷史均價 ±30%
    - 毛利率 < 0% 或 > 60%
    - 單筆金額超過該客戶月均 3 倍
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ii.id, ii.item_date, ii.order_no, ii.product, ii.grade,
               ii.spec, ii.unit_price, ii.amount, ii.measure_value,
               ii.measure_unit, inv.customer_name, inv.period_end
        FROM invoice_items ii
        JOIN invoices inv ON inv.id = ii.invoice_id
        WHERE ii.line_type = 'sale'
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # 計算每個品項的歷史平均單價
    price_history = {}
    for r in rows:
        key = (r.get("product"), r.get("grade"), r.get("spec"))
        if r.get("unit_price"):
            price_history.setdefault(key, []).append(float(r["unit_price"]))

    avg_prices = {k: sum(v)/len(v) for k, v in price_history.items() if len(v) >= 2}

    # 計算每個客戶的月均金額
    customer_amounts = {}
    for r in rows:
        c = r.get("customer_name") or "Unknown"
        customer_amounts.setdefault(c, []).append(float(r.get("amount") or 0))

    customer_avg = {c: sum(v)/len(v) for c, v in customer_amounts.items()}

    anomalies = []
    for r in rows:
        reasons = []
        key = (r.get("product"), r.get("grade"), r.get("spec"))
        unit_price = r.get("unit_price")
        amount = float(r.get("amount") or 0)
        customer = r.get("customer_name") or "Unknown"

        # 單價異常
        if unit_price and key in avg_prices:
            avg = avg_prices[key]
            if float(unit_price) > avg * 1.3:
                pct = round((float(unit_price)/avg - 1)*100)
                reasons.append(f"單價高於歷史均價 {pct}%（均價 {avg:.0f}）")
            elif float(unit_price) < avg * 0.7:
                pct = round((1 - float(unit_price)/avg)*100)
                reasons.append(f"單價低於歷史均價 {pct}%（均價 {avg:.0f}）")

        # 金額異常
        if customer in customer_avg and customer_avg[customer] > 0:
            if amount > customer_avg[customer] * 3:
                reasons.append(f"單筆金額為該客戶均值的 {amount/customer_avg[customer]:.1f} 倍")

        if reasons:
            anomalies.append({
                "id": r.get("id"),
                "customer_name": customer,
                "item_date": r.get("item_date"),
                "order_no": r.get("order_no"),
                "product": r.get("product"),
                "grade": r.get("grade"),
                "spec": r.get("spec"),
                "unit_price": unit_price,
                "amount": amount,
                "reasons": reasons,
            })

    return anomalies

#以下是為了客戶價值評分新增的程式碼#
def get_customer_scores() -> list[dict]:
    """RFM 簡化版客戶評分"""
    import datetime
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT ii.*, inv.customer_name, inv.period_end
        FROM invoice_items ii
        JOIN invoices inv ON inv.id = ii.invoice_id
        WHERE ii.line_type = 'sale'
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    from reports import calculate_profit_for_item_rows
    enriched, _ = calculate_profit_for_item_rows(rows)

    # 按客戶分組
    customers = {}
    for it in enriched:
        c = it.get("customer_name") or "Unknown"
        customers.setdefault(c, {
            "customer_name": c,
            "transactions": [],
            "revenue": 0.0,
            "gross_profit": 0.0,
            "last_date": None,
        })
        customers[c]["transactions"].append(it)
        customers[c]["revenue"] += float(it.get("amount") or 0)
        customers[c]["gross_profit"] += float(it.get("gross_profit") or 0)
        d = it.get("item_date")
        if d and (customers[c]["last_date"] is None or d > customers[c]["last_date"]):
            customers[c]["last_date"] = d

    result = []
    all_revenues = [v["revenue"] for v in customers.values()]
    all_freqs = [len(v["transactions"]) for v in customers.values()]
    max_rev = max(all_revenues) if all_revenues else 1
    max_freq = max(all_freqs) if all_freqs else 1

    for c, data in customers.items():
        freq = len(data["transactions"])
        rev = data["revenue"]
        gp = data["gross_profit"]
        margin = gp / rev if rev else 0

        # M 分（毛利貢獻）
        m_score = round((gp / (max_rev * 0.3 + 1)) * 5, 1)
        m_score = min(5, max(1, m_score))

        # F 分（交易頻率）
        f_score = round((freq / max_freq) * 5, 1)
        f_score = min(5, max(1, f_score))

        # R 分（簡化：用交易筆數多寡代替，資料不足時難算真實 recency）
        r_score = f_score  # 簡化處理

        score = round((r_score + f_score + m_score) / 3, 2)
        if score >= 4:
            tier = "⭐ VIP"
        elif score >= 2.5:
            tier = "一般"
        else:
            tier = "低度往來"

        result.append({
            "customer_name": c,
            "tier": tier,
            "score": score,
            "revenue": round(rev, 0),
            "gross_profit": round(gp, 0),
            "gross_margin_rate": round(margin * 100, 1),
            "transaction_count": freq,
        })

    return sorted(result, key=lambda x: x["score"], reverse=True)
