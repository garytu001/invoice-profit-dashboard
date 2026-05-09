"""
Invoice parsing logic for the GP Dashboard.
Contains functions for converting raw OCR lines to structured invoice items.
"""

from utils import parse_raw_line, looks_like_spec
from image_utils import extract_raw_lines_with_gpt


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
