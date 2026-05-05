"""
Utility functions for text parsing and tokenization used in invoice processing.
These functions handle low-level string manipulation, regex matching, and data extraction.
"""

import re


def is_date_token(s: str) -> bool:
    """判斷是否為 mm/dd（例如 11/13）。"""
    return bool(re.fullmatch(r"\d{1,2}/\d{1,2}", s.strip()))


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