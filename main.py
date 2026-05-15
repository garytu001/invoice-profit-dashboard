import os
import json
import csv
import io
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from db import init_db, get_conn
from profit_calculator import calculate_profit_for_invoice, find_cost_for_item
from image_utils import ensure_supported_image_mime, extract_raw_lines_with_gpt, resolve_mime_type
from utils import parse_raw_line, looks_like_spec
from invoice_parser import (
    parse_invoice_with_gpt,
    deduplicate_items,
    normalize_unit_price,
    mark_suspicious_items,
    apply_unit_consistency_warnings,
    summarize_warnings,
)
from models import ConfirmPayload, CostOverridePayload, ProfitCalcPayload, CostRowPayload, ItemCostOverridePayload
import invoice_service as svc
from reports import get_dashboard_data
from exports import export_items_csv as export_items_csv_func, export_summary_csv as export_summary_csv_func
from reports import calculate_profit_for_item_rows

@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時初始化資料庫 schema 與必要 migration。"""
    init_db()
    yield

# FastAPI 應用：提供請款單上傳、OCR/結構化解析、資料落庫
app = FastAPI(title="Invoice Gross Profit Dashboard", lifespan=lifespan)

# OpenAI SDK client is initialized in backend/image_utils.py using OPENAI_API_KEY
# CORS：目前全開方便前端開發，正式上線建議鎖來源網域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/upload")
async def upload_invoice(files: List[UploadFile] = File(...)):
    """
    上傳請款單影像並完成整條流程：
    1) 讀檔
    2) GPT 解析抬頭與 raw_lines
    3) Python 規則解析 raw_lines -> items
    4) 存入 DB
    5) 回傳解析結果與統計
    """
    results = []
    for file in files:
        try:
            # 1) 讀取圖片 bytes
            content = await file.read()
            if len(content) == 0:
                results.append({"filename": file.filename, "success": False, "error": "Empty file"})
                continue
            mime_type = resolve_mime_type(file, content)
            ensure_supported_image_mime(mime_type)

            # 2) 呼叫 GPT API 解析
            parsed = parse_invoice_with_gpt(content, mime_type=mime_type)

            # 3) 存到 DB
            invoice_id = svc.save_invoice_to_db(parsed, source_filename=file.filename)

            results.append({
                "filename": file.filename,
                "success": True,
                "invoice_id": invoice_id,
                "parsed": parsed,
                "raw_lines_count": len(parsed.get("raw_lines", [])),
                "item_count": len(parsed.get("items", [])),
                "warning_summary": parsed.get("warning_summary", {}),
            })
        except Exception as e:
            results.append({"filename": file.filename, "success": False, "error": str(e)})
    return {"results": results}


@app.post("/api/parse-preview")
async def parse_preview(files: List[UploadFile] = File(...)):
    """
    只解析不入庫，供 UI 先做人工作業確認。
    """
    results = []
    for file in files:
        try:
            content = await file.read()
            if len(content) == 0:
                results.append({"filename": file.filename, "success": False, "error": "Empty file"})
                continue
            mime_type = resolve_mime_type(file, content)
            ensure_supported_image_mime(mime_type)

            parsed = parse_invoice_with_gpt(content, mime_type=mime_type)
            results.append({
                "filename": file.filename,
                "success": True,
                "parsed": parsed,
                "raw_lines_count": len(parsed.get("raw_lines", [])),
                "item_count": len(parsed.get("items", [])),
                "warning_summary": parsed.get("warning_summary", {}),
            })
        except Exception as e:
            results.append({"filename": file.filename, "success": False, "error": str(e)})
    return {"results": results}


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

    invoice_id = svc.save_invoice_to_db(parsed, source_filename=payload.source_filename)
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


app.get("/api/costs")(svc.list_costs)
app.post("/api/costs")(svc.create_cost)
app.put("/api/costs/{cost_id}")(svc.update_cost)
app.delete("/api/costs/{cost_id}")(svc.delete_cost)


@app.post("/api/costs/import-csv")
async def import_costs_csv(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    return svc.import_costs_csv(content)


app.get("/api/reports/dashboard")(get_dashboard_data)
app.post("/api/reports/item-cost-override")(svc.set_item_cost_override)
app.get("/api/reports/item-cost-overrides")(svc.list_item_cost_overrides)
app.delete("/api/reports/item-cost-override/{invoice_item_id}")(svc.delete_item_cost_override)

app.get("/api/export/items.csv")(export_items_csv_func)
app.get("/api/export/summary.csv")(export_summary_csv_func)
