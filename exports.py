import csv
import io
from fastapi.responses import StreamingResponse
from db import get_conn
from reports import get_dashboard_data


def export_items_csv(invoice_id: int | None = None):
    conn = get_conn()
    cur = conn.cursor()
    if invoice_id is None:
        cur.execute(
            """
            SELECT ii.*, inv.customer_name, inv.print_date, inv.period_start, inv.period_end
            FROM invoice_items ii
            JOIN invoices inv ON inv.id = ii.invoice_id
            ORDER BY ii.id ASC
            """
        )
    else:
        cur.execute(
            """
            SELECT ii.*, inv.customer_name, inv.print_date, inv.period_start, inv.period_end
            FROM invoice_items ii
            JOIN invoices inv ON inv.id = ii.invoice_id
            WHERE ii.invoice_id = ?
            ORDER BY ii.id ASC
            """,
            (invoice_id,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "invoice_id",
            "customer_name",
            "item_date",
            "order_no",
            "line_type",
            "product",
            "grade",
            "spec",
            "qty",
            "measure_value",
            "measure_unit",
            "unit_price",
            "amount",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.get("invoice_id"),
                r.get("customer_name"),
                r.get("item_date"),
                r.get("order_no"),
                r.get("line_type"),
                r.get("product"),
                r.get("grade"),
                r.get("spec"),
                r.get("qty"),
                r.get("measure_value"),
                r.get("measure_unit"),
                r.get("unit_price"),
                r.get("amount"),
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoice_items.csv"},
    )


def export_summary_csv(period: str = "month"):
    data = get_dashboard_data(period=period)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["分類", "鍵值", "營收", "銷貨成本", "毛利", "毛利率"])

    for row in data["trend"]:
        writer.writerow(["trend", row.get("bucket"), row.get("revenue"), row.get("cogs"), row.get("gross_profit"), row.get("gross_margin_rate")])
    for row in data["by_customer"]:
        writer.writerow(
            ["customer", row.get("customer_name"), row.get("revenue"), row.get("cogs"), row.get("gross_profit"), row.get("gross_margin_rate")]
        )
    for row in data["by_item"]:
        writer.writerow(["item", row.get("item_key"), row.get("revenue"), row.get("cogs"), row.get("gross_profit"), row.get("gross_margin_rate")])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=summary_{period}.csv"},
    )
