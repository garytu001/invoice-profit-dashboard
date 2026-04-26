import os
import json
import re
import base64
import csv
import io
from pathlib import Path
from openai import OpenAI
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from db import init_db, get_conn
from profit_calculator import calculate_profit_for_invoice, find_cost_for_item

# FastAPI 應用：提供請款單上傳、OCR/結構化解析、資料落庫
app = FastAPI(title="Invoice Gross Profit Dashboard")

# OpenAI SDK client：使用環境變數 OPENAI_API_KEY
client = OpenAI()
ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# CORS：目前全開方便前端開發，正式上線建議鎖來源網域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """啟動時初始化資料庫 schema 與必要 migration。"""
    init_db()


@app.get("/api/health")
def health():
    """健康檢查端點。"""
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def review_page():
    """人工覆核頁面。"""
    html_path = Path(__file__).parent / "review.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="review.html not found")
    return html_path.read_text(encoding="utf-8")


@app.get("/app", response_class=HTMLResponse)
def app_page():
    """整合網站頁面。"""
    html_path = Path(__file__).parent / "webapp.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="webapp.html not found")
    return html_path.read_text(encoding="utf-8")


class ConfirmPayload(BaseModel):
    source_filename: str = Field(default="manual-review")
    parsed: dict


class CostOverridePayload(BaseModel):
    product: str
    grade: str | None = None
    spec: str | None = None
    cost_per_unit: float
    cost_unit: str


class ProfitCalcPayload(BaseModel):
    invoice_id: int
    cost_overrides: list[CostOverridePayload] = Field(default_factory=list)


class CostRowPayload(BaseModel):
    product: str
    grade: str | None = None
    spec: str | None = None
    cost_per_unit: float
    cost_unit: str = "才"
    effective_from: str | None = None


class ItemCostOverridePayload(BaseModel):
    invoice_item_id: int
    cost_per_unit: float
    cost_unit: str


@app.post("/api/upload")
async def upload_invoice(file: UploadFile = File(...)):
    """
    上傳請款單影像並完成整條流程：
    1) 讀檔
    2) GPT 解析抬頭與 raw_lines
    3) Python 規則解析 raw_lines -> items
    4) 存入 DB
    5) 回傳解析結果與統計
    """
    # 1) 讀取圖片 bytes
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    mime_type = resolve_mime_type(file, content)
    ensure_supported_image_mime(mime_type)

    # 2) 呼叫 GPT API 解析
    try:
        parsed = parse_invoice_with_gpt(content, mime_type=mime_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT parse failed: {e}")

    # 3) 存到 DB
    invoice_id = save_invoice_to_db(parsed, source_filename=file.filename)

    return {
        "invoice_id": invoice_id,
        "parsed": parsed,
        "raw_lines_count": len(parsed.get("raw_lines", [])),
        "item_count": len(parsed.get("items", [])),
        "warning_summary": parsed.get("warning_summary", {}),
    }


@app.post("/api/parse-preview")
async def parse_preview(file: UploadFile = File(...)):
    """
    只解析不入庫，供 UI 先做人工作業確認。
    """
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    mime_type = resolve_mime_type(file, content)
    ensure_supported_image_mime(mime_type)

    try:
        parsed = parse_invoice_with_gpt(content, mime_type=mime_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT parse failed: {e}")

    return {
        "parsed": parsed,
        "raw_lines_count": len(parsed.get("raw_lines", [])),
        "item_count": len(parsed.get("items", [])),
        "warning_summary": parsed.get("warning_summary", {}),
    }


@app.post("/api/confirm")
def confirm_invoice(payload: ConfirmPayload):
    """
    使用者在 UI 修正後，再由此端點正式入庫。
    """
    parsed = payload.parsed or {}
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid parsed payload")

    items = parsed.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="parsed.items must be a list")

    parsed["items"] = deduplicate_items(items)
    parsed["items"] = normalize_unit_price(parsed["items"])
    parsed["items"] = mark_suspicious_items(parsed["items"])
    parsed["items"] = apply_unit_consistency_warnings(parsed["items"])
    parsed["warning_summary"] = summarize_warnings(parsed["items"])

    invoice_id = save_invoice_to_db(parsed, source_filename=payload.source_filename)
    return {
        "ok": True,
        "invoice_id": invoice_id,
        "item_count": len(parsed["items"]),
        "warning_summary": parsed.get("warning_summary", {}),
    }


@app.post("/api/profit/calculate")
def calculate_profit(payload: ProfitCalcPayload):
    """
    利潤計算 API（演算法實作於 profit_calculator.py）。
    """
    conn = get_conn()
    result = calculate_profit_for_invoice(
        conn=conn,
        invoice_id=payload.invoice_id,
        cost_overrides=[x.model_dump() for x in payload.cost_overrides],
    )
    conn.close()

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/costs")
def list_costs():
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


@app.post("/api/costs")
def create_cost(payload: CostRowPayload):
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


@app.put("/api/costs/{cost_id}")
def update_cost(cost_id: int, payload: CostRowPayload):
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


@app.delete("/api/costs/{cost_id}")
def delete_cost(cost_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM cost_table WHERE id = ?", (cost_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Cost row {cost_id} not found")
    return {"ok": True}


@app.post("/api/costs/import-csv")
async def import_costs_csv(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

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


def parse_roc_date_text(s: str | None) -> tuple[int, int, int] | None:
    """
    解析民國日期字串（例如 115.01.31 / 115/01/31）並回傳西元 (year, month, day)。
    """
    if not s:
        return None
    m = re.search(r"(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})", str(s).strip())
    if not m:
        return None
    roc_y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return roc_y + 1911, mm, dd


def infer_txn_ym(item_date: str | None, period_end: str | None, created_at: str | None) -> tuple[int, int] | None:
    """
    由 item_date(mm/dd) + period_end 推回交易年月。
    若推不出來，退回 created_at 的年月。
    """
    item_m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\s*", str(item_date or ""))
    end_dt = parse_roc_date_text(period_end)
    if item_m and end_dt:
        mm = int(item_m.group(1))
        end_y, end_m, _ = end_dt
        # 請款期間跨年時，月份大於 period_end 月份通常屬於前一年
        yy = end_y - 1 if mm > end_m else end_y
        return yy, mm

    # fallback: created_at(YYYY-MM-DD ...)
    created = str(created_at or "")
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-\d{2}.*", created)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


@app.get("/api/reports/dashboard")
def get_dashboard(period: str = "month"):
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


@app.post("/api/reports/item-cost-override")
def set_item_cost_override(payload: ItemCostOverridePayload):
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


@app.get("/api/reports/item-cost-overrides")
def list_item_cost_overrides():
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


@app.delete("/api/reports/item-cost-override/{invoice_item_id}")
def delete_item_cost_override(invoice_item_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM item_cost_overrides WHERE invoice_item_id = ?", (invoice_item_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"override for invoice_item_id={invoice_item_id} not found")
    return {"ok": True}


@app.get("/api/export/items.csv")
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


@app.get("/api/export/summary.csv")
def export_summary_csv(period: str = "month"):
    data = get_dashboard(period=period)
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


def is_date_token(s: str) -> bool:
    """判斷是否為 mm/dd（例如 11/13）。"""
    return bool(re.fullmatch(r"\d{1,2}/\d{1,2}", s.strip()))


def resolve_mime_type(file: UploadFile, content: bytes) -> str:
    """
    優先讀取 UploadFile.content_type，若缺失則以檔頭 bytes 推測。
    """
    content_type = (file.content_type or "").lower().strip()
    if content_type:
        return content_type

    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF"):
        return "application/pdf"
    return "application/octet-stream"


def ensure_supported_image_mime(mime_type: str) -> None:
    """
    限制為 OpenAI vision 支援格式。
    """
    if mime_type in ALLOWED_IMAGE_MIME:
        return
    if mime_type == "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="PDF is not supported for parsing. Please upload JPEG/PNG/GIF/WEBP.",
        )
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type: {mime_type}. Please upload JPEG/PNG/GIF/WEBP.",
    )


def is_order_no_token(s: str) -> bool:
    """判斷是否為 5~7 碼單號。"""
    return bool(re.fullmatch(r"\d{5,7}", s.strip()))


def looks_like_spec(s: str) -> bool:
    """判斷是否為規格格式（例如 132x80x12）。"""
    s = s.strip().lower().replace(" ", "")
    return bool(re.fullmatch(r"\d+x\d+x\d+", s))


def clean_token(s: str) -> str:
    """文字清洗：去前後空白 + 中文標點標準化。"""
    return s.strip().replace("，", ",").replace("：", ":")


def extract_raw_lines_with_gpt(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    呼叫 GPT Vision 把圖片轉成結構化 JSON：
    - 抬頭欄位：print_date, period_start, period_end, customer_name
    - 明細原文：raw_lines

    注意：這一步只做「忠實轉錄」，不做金額/欄位語義計算。
    """
    prompt = """
You must return valid JSON only.

請解析這張繁體中文請款單圖片，並輸出 JSON。
輸出格式必須是 json object，格式如下：

{
  "print_date": string|null,
  "period_start": string|null,
  "period_end": string|null,
  "customer_name": string|null,
  "raw_lines": [string, string, ...]
}

任務分成兩部分：

第一部分：讀取抬頭區域，找出：
1. print_date（印表日期）
2. period_start（請款期間起）
3. period_end（請款期間迄）
4. customer_name（客戶名稱，不是開立請款單的公司名稱）

第二部分：逐行轉錄明細表。
請把每一筆明細原樣轉錄到 raw_lines。

重要規則：
1. raw_lines 只放明細資料列，不放表頭。
2. 每一行請盡量完整保留，不要省略欄位。
3. raw_lines 只保留每一筆明細資料，不要保留出貨小計。
4. 若某行最後的數字是該組小計而不是單筆金額，請不要輸出該小計。
5. 不要自行理解欄位意義，不要重組欄位，只要忠實轉錄。
6. 若某列看起來跨行，請盡量合併成同一列。
7. 請確保 raw_lines 包含所有可見明細列，不要只擷取部分。
8. 如果表格有多列，請完整輸出全部，不要截斷。
9. 如果同一個單號出現多筆不同規格，必須將每一筆規格各自保留為一條 raw_lines。
10. 不可以因為單號相同就把多筆資料合併成一行。
11. 即使日期、單號、品名、等級相同，只要規格不同，就一定要分成不同列。
12. 同一單號下的每一筆規格、數量、才數/坪數、單價、金額都要完整記錄。
13. 規格（spec）只能是數字格式，僅允許像 132x80x12、80x32x033 這種由數字與 x 組成的內容。
14. 等級（grade）可以為空白，或是「X尺」格式（例如 A尺、B尺、上尺、中尺、下尺），也可以只有「尺」。
15. 單位只能忠實抄寫原文：看到「坪」就輸出坪，看到「才」就輸出才，禁止自行替換或推測。
16. 若單位看不清楚，請輸出 null，不要猜成才或坪。
17. 只輸出 valid JSON，不要輸出任何說明文字，不要用 markdown code fence。
18. 品號名稱大致如下：美檜、日檜、壁板日檜、實木、日檜直拼板、貼皮板
19. 公司名稱為億立可有限公司
20. 沒有才數/坪數的話寫 null
21. 才數/坪數的數值（measure_value）也可以是 null，不可自行補值。
22. 前面沒有日期的話延續最上一筆的日期（切勿忘記）

範例：
{
  "print_date": "115/01/05",
  "period_start": "114.11.01",
  "period_end": "114.12.31",
  "customer_name": "加工富哥",
  "raw_lines": [
    "11/13 113166 日檜 上尺 132x80x12 3 38.02才 200 7604",
    "11/13 113166 日檜 上尺 132x75x12 1 11.88才 200 2376"
  ]
}
"""

    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    response = client.responses.create(
        model="gpt-5.1",
        reasoning={"effort": "low"},
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{base64_image}",
                    },
                ],
            }
        ],
        text={"format": {"type": "json_object"}},
    )

    # SDK 提供的純文字輸出（理想上是 JSON 字串）
    text = response.output_text.strip()

    # 保底：若模型偶發輸出 markdown fence，先清掉再 json.loads
    if text.startswith("```"):
        text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE)

    data = json.loads(text)
    if "raw_lines" not in data or not isinstance(data["raw_lines"], list):
        data["raw_lines"] = []

    return data


def is_number_token(s: str) -> bool:
    """
    判斷是否數字 token，可帶單位：
    - 95.30
    - 95.30才
    - 10.75坪
    """
    s = s.replace(",", "").strip()
    return bool(re.fullmatch(r"-?\d+(\.\d+)?(才|坪)?", s))


def parse_number_token(s: str) -> tuple[float, str | None] | None:
    """
    把數字 token 拆成 (value, unit)：
    - '38.02才' -> (38.02, '才')
    - '200' -> (200.0, None)
    """
    s = s.replace(",", "").strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(才|坪)?", s)
    if not m:
        return None

    value = float(m.group(1))
    unit = m.group(2)
    return value, unit


def merge_split_unit_tokens(parts: list[str]) -> list[str]:
    """
    把分開的單位 token 併回去：
    例如 ["17.07", "坪"] -> ["17.07坪"]。
    """
    merged = []
    i = 0
    while i < len(parts):
        cur = parts[i].strip()
        nxt = parts[i + 1].strip() if i + 1 < len(parts) else None

        if nxt in {"才", "坪"} and re.fullmatch(r"-?\d+(?:\.\d+)?", cur):
            merged.append(f"{cur}{nxt}")
            i += 2
            continue

        merged.append(cur)
        i += 1

    return merged


def parse_raw_line(line: str, prev_context: dict | None = None) -> dict | None:
    """
    將單行 raw text 解析成結構化 item。

    支援兩種行型：
    1) 標準列：含 date + order_no
    2) 續行列：不含 date/order_no，沿用上一筆 context

    解析策略：
    - 由尾端往前抓數字區塊（qty/measure/unit_price/amount）
    - 中段文字區塊推 product/grade/spec
    - 最後回傳標準 item 欄位
    """
    line = clean_token(line)
    parts = [p for p in line.split() if p.strip()]
    parts = merge_split_unit_tokens(parts)

    if len(parts) < 4:
        return None

    date_token = None
    order_no_token = None
    start_idx = 0

    # 情況 A：標準列，有日期 + 單號
    if len(parts) >= 2 and is_date_token(parts[0]) and is_order_no_token(parts[1]):
        date_token = parts[0]
        order_no_token = parts[1]
        start_idx = 2

    # 情況 B：續行列，沿用上一列的日期 / 單號
    elif prev_context is not None:
        date_token = prev_context.get("date")
        order_no_token = prev_context.get("order_no")
        start_idx = 0

    else:
        return None

    # 從尾端抓數字（可接受 才 / 坪），直到遇到非數字 token 停止
    numeric_tail = []
    idx = len(parts) - 1
    while idx >= start_idx and is_number_token(parts[idx]):
        numeric_tail.append(parts[idx].strip())
        idx -= 1
    numeric_tail.reverse()

    # 至少要有 qty + unit_price + amount
    if len(numeric_tail) < 3:
        return None

    # 中間剩下的是商品描述區
    middle = parts[start_idx : idx + 1]
    has_return_marker = "退" in middle
    if has_return_marker:
        middle = [t for t in middle if t != "退"]
    product, grade, spec = None, None, None

    # 找 spec（像 132x38x38）
    spec_index = next((i for i, t in enumerate(middle) if looks_like_spec(t)), None)

    if spec_index is not None:
        spec = middle[spec_index]
        before = middle[:spec_index]
        after = middle[spec_index + 1 :]

        if len(before) >= 1:
            product = before[0]

        # 合併「上 尺 / 中 尺 / 下 尺」等級寫法為「上尺 / 中尺 / 下尺」
        if len(before) >= 3 and before[1] in {"上", "中", "下", "B", "E"} and before[2] == "尺":
            grade = before[1] + before[2]
        elif len(before) >= 2:
            grade = before[1]

        # 偶發情況：spec 前面沒有 product，嘗試用 spec 後第一詞補 product
        if not product and after:
            product = after[0]

    else:
        # 找不到 spec 時，盡量兜底拆欄位，避免整行丟失
        if len(middle) >= 1:
            product = middle[0]

        if len(middle) >= 3 and middle[1] in {"上", "中", "下", "B", "E"} and middle[2] == "尺":
            grade = middle[1] + middle[2]
            if len(middle) >= 4:
                spec = " ".join(middle[3:])
        else:
            if len(middle) >= 2:
                grade = middle[1]
            if len(middle) >= 3:
                spec = " ".join(middle[2:])

    # 解析尾端數字，分離 measure_value / measure_unit
    try:
        parsed_tail = [parse_number_token(x) for x in numeric_tail]
        if any(x is None for x in parsed_tail):
            return None

        # 經過前面檢查後，每個元素都不是 None
        parsed_tail = [x for x in parsed_tail if x is not None]

        qty = None
        measure_value = None
        measure_unit = None
        unit_price = None
        amount = None

        # 出貨小計通常會出現在行尾，若尾端超過 4 個數字，先忽略最末一個小計。
        core_tail = parsed_tail[:-1] if len(parsed_tail) >= 5 else parsed_tail

        if len(core_tail) < 3:
            return None

        if len(core_tail) == 3:
            # qty, unit_price, amount
            qty = core_tail[0][0]
            unit_price = core_tail[1][0]
            amount = core_tail[2][0]

        else:
            # 通用策略：
            # - 第一個視為 qty
            # - 最後兩個固定視為 unit_price 與 amount
            qty = core_tail[0][0]
            unit_price = core_tail[-2][0]
            amount = core_tail[-1][0]

            # 中間區段視為 measure 候選（優先挑帶「才/坪」單位者）
            measure_candidates = core_tail[1:-2]
            if measure_candidates:
                picked = next((x for x in measure_candidates if x[1] in {"才", "坪"}), measure_candidates[-1])
                measure_value = picked[0]
                measure_unit = picked[1]

    except Exception:
        return None

    return {
        "date": date_token,
        "order_no": order_no_token,
        "line_type": "return" if has_return_marker or (qty is not None and qty < 0) or (amount is not None and amount < 0) else "sale",
        "product": product,
        "grade": grade,
        "spec": spec,
        "qty": qty,
        "measure_value": measure_value,
        "measure_unit": measure_unit,
        "unit_price": unit_price,
        "amount": amount,
    }


def convert_raw_lines_to_items(raw_lines: list[str]) -> list[dict]:
    """
    逐行解析 raw_lines。
    - 會維護 prev_context，支援無日期/單號的續行列。
    """
    items = []
    prev_context = None

    for line in raw_lines:
        parsed = parse_raw_line(line, prev_context=prev_context)
        if parsed is not None:
            items.append(parsed)

            # 只有真的有日期與單號時，才更新 context
            if parsed.get("date") and parsed.get("order_no"):
                prev_context = {
                    "date": parsed.get("date"),
                    "order_no": parsed.get("order_no"),
                }

    return items


def deduplicate_items(items: list[dict]) -> list[dict]:
    """刪除完全重複列（全欄位相同才視為重複）。"""
    seen = set()
    result = []

    for it in items:
        key = (
            it.get("date"),
            it.get("order_no"),
            it.get("line_type"),
            it.get("product"),
            it.get("grade"),
            it.get("spec"),
            it.get("qty"),
            it.get("measure_value"),
            it.get("measure_unit"),
            it.get("unit_price"),
            it.get("amount"),
        )
        if key not in seen:
            seen.add(key)
            result.append(it)

    return result


def apply_unit_consistency_warnings(items: list[dict]) -> list[dict]:
    """
    依 (product, grade, spec) 統計主流單位，若少數列單位不同，標記 unit_suspicious。
    """
    stats = {}
    for it in items:
        unit = it.get("measure_unit")
        if unit not in {"才", "坪"}:
            continue
        key = (it.get("product"), it.get("grade"), it.get("spec"))
        unit_counter = stats.setdefault(key, {"才": 0, "坪": 0})
        unit_counter[unit] += 1

    dominant = {}
    for key, counter in stats.items():
        total = counter["才"] + counter["坪"]
        if total < 3:
            continue
        dominant_unit = "才" if counter["才"] > counter["坪"] else "坪"
        share = counter[dominant_unit] / total
        if share >= 0.8:
            dominant[key] = dominant_unit

    for it in items:
        unit = it.get("measure_unit")
        key = (it.get("product"), it.get("grade"), it.get("spec"))
        dom = dominant.get(key)
        if dom and unit in {"才", "坪"} and unit != dom:
            it.setdefault("warnings", []).append(f"unit_suspicious: expected {dom}, got {unit}")

    return items


def normalize_unit_price(items: list[dict]) -> list[dict]:
    """
    修正常見 OCR 錯誤：單價少一個 0（或兩個 0）。
    用 amount / measure_value 回推合理單價，僅在非常接近 10x/100x 時自動修正。
    """
    for it in items:
        measure_value = it.get("measure_value")
        unit_price = it.get("unit_price")
        amount = it.get("amount")

        try:
            if measure_value in (None, 0) or unit_price in (None, 0) or amount is None:
                continue

            inferred = abs(float(amount)) / abs(float(measure_value))
            current = abs(float(unit_price))

            ratio = inferred / current if current else 0
            new_price = None
            if 9.6 <= ratio <= 10.4:
                new_price = current * 10
            elif 96 <= ratio <= 104:
                new_price = current * 100

            if new_price is not None:
                # 盡量保留正值單價，依原本正負號回寫
                signed = -new_price if float(unit_price) < 0 else new_price
                it["unit_price"] = round(signed, 2)
                it.setdefault("warnings", []).append("unit_price_auto_fixed_by_amount")
        except Exception:
            continue

    return items


def mark_suspicious_items(items: list[dict]) -> list[dict]:
    """
    對每筆資料打 warning：
    - 格式疑慮（spec/product）
    - 數值異常（qty/measure/unit_price/amount）
    - 欄位缺失（order_no/spec）

    設計原則：只「標記」不丟棄，讓前端與人工可以追查。
    """
    for it in items:
        warnings = []

        qty = it.get("qty")
        line_type = it.get("line_type", "sale")
        measure_value = it.get("measure_value")
        measure_unit = it.get("measure_unit")
        unit_price = it.get("unit_price")
        amount = it.get("amount")
        spec = it.get("spec")
        product = it.get("product")
        order_no = it.get("order_no")

        if spec is not None and not looks_like_spec(str(spec)):
            warnings.append("spec format unusual")

        if product is not None and looks_like_spec(str(product)):
            warnings.append("product may actually be spec")

        if qty is not None:
            try:
                if line_type == "sale" and qty <= 0:
                    warnings.append("qty <= 0")
                elif qty > 200:
                    warnings.append("qty unusually large")
            except Exception:
                warnings.append("qty invalid")

        if measure_value is not None:
            try:
                if measure_value <= 0:
                    warnings.append("measure_value <= 0")
                elif measure_value > 1000:
                    warnings.append("measure_value unusually large")
            except Exception:
                warnings.append("measure_value invalid")

        if measure_unit is not None and measure_unit not in {"才", "坪"}:
            warnings.append("measure_unit unusual")

        if unit_price is not None:
            try:
                if unit_price <= 0:
                    warnings.append("unit_price <= 0")
                elif unit_price > 10000:
                    warnings.append("unit_price unusually large")
            except Exception:
                warnings.append("unit_price invalid")

        if amount is not None:
            try:
                if line_type == "sale" and amount <= 0:
                    warnings.append("amount <= 0")
            except Exception:
                warnings.append("amount invalid")

        # 啟發式：只有 1 片/支 但度量值極大，通常資料欄位錯位
        if qty is not None and measure_value is not None:
            try:
                if qty == 1 and measure_value > 500:
                    warnings.append("qty=1 but measure_value unusually large")
            except Exception:
                pass

        # amount 理論上通常 >= unit_price（至少 1 單位）
        if amount is not None and unit_price is not None:
            try:
                if amount < unit_price:
                    warnings.append("amount smaller than unit_price")
            except Exception:
                pass

        if not order_no:
            warnings.append("missing order_no")

        if not spec:
            warnings.append("missing spec")

        it["warnings"] = warnings

    return items


def summarize_warnings(items: list[dict]) -> dict:
    """彙整 warning：有警示筆數 + 各類型次數。"""
    count_lines_with_warning = 0
    warning_types = {}

    for it in items:
        ws = it.get("warnings", [])
        if ws:
            count_lines_with_warning += 1
            for w in ws:
                warning_types[w] = warning_types.get(w, 0) + 1

    return {
        "warning_line_count": count_lines_with_warning,
        "warning_types": warning_types,
    }


def parse_invoice_with_gpt(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    解析主流程：
    GPT 影像轉 raw_lines -> 規則解析 items -> 去重 -> 標記可疑 -> 警示摘要
    """
    raw_data = extract_raw_lines_with_gpt(image_bytes, mime_type=mime_type)

    items = convert_raw_lines_to_items(raw_data.get("raw_lines", []))
    items = deduplicate_items(items)
    items = normalize_unit_price(items)
    items = mark_suspicious_items(items)
    items = apply_unit_consistency_warnings(items)

    warning_summary = summarize_warnings(items)

    return {
        "print_date": raw_data.get("print_date"),
        "period_start": raw_data.get("period_start"),
        "period_end": raw_data.get("period_end"),
        "customer_name": raw_data.get("customer_name"),
        "items": items,
        "raw_lines": raw_data.get("raw_lines", []),
        "warning_summary": warning_summary,
    }
