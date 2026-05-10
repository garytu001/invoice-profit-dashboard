"""
This module provides utilities for handling image uploads and extracting structured data from images using OpenAI's GPT Vision.
Key functionalities include:
1. Resolving MIME types from uploaded files, with a fallback to content-based detection.
2. Validating that the MIME type is supported for image processing.
3. Extracting structured data from images using GPT Vision, specifically designed for parsing invoice-like documents with fields such as print date, period start/end, customer name, and raw line items.
The main function, `extract_raw_lines_with_gpt`, takes image bytes and an optional MIME type, constructs a prompt for GPT Vision, and processes the response to return a structured JSON object containing the extracted data.
The module relies on the OpenAI Python client for interacting with the GPT Vision API and FastAPI for handling HTTP exceptions.
"""


import base64
import json
import re
from fastapi import HTTPException, UploadFile
from openai import OpenAI

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

client = OpenAI()


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

第一部分：讀取抬頭區域，請務必精確抓取：
1. print_date（印表日期）
2. period_start（請款期間起）（例如 115/01/01）
3. period_end（請款期間迄）（例如 115/01/31）
4. customer_name（客戶名稱，不是開立請款單的公司名稱）
以上欄位對於後續年份計算至關重要。

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
9. 若某列看起來像是「合計」、「小計」、「總條」、「總計」開頭，請不要輸出該列。
10. raw_lines 的每一行，第一個欄位一定是日期（mm/dd格式），第二個欄位一定是單號（5-7位數字），若不符合請勿輸出。"""

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

    text = response.output_text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE)

    data = json.loads(text)
    if "raw_lines" not in data or not isinstance(data["raw_lines"], list):
        data["raw_lines"] = []

    return data

