"""
綜合損益表計算模組
參考台灣 IFRS 財報格式
"""
from reports import get_dashboard_data
from expenses_service import get_expense_summary


EXPENSE_CATEGORIES = [
    "薪資費用",
    "運費",
    "租金費用",
    "折舊費用",
    "廣告費用",
    "雜項費用",
]


def build_income_statement(period: str = "month", year_month: str = None) -> dict:
    """
    建立完整綜合損益表
    
    台灣 IFRS 格式：
    營業收入
    - 銷貨成本
    = 毛利（毛損）
    - 營業費用
      - 薪資費用
      - 運費
      - 租金費用
      - 其他費用
    = 營業利益（損失）
    + 營業外收入（目前無資料，留空）
    - 營業外費用（目前無資料，留空）
    = 稅前淨利（損失）
    - 所得稅費用（目前無資料，留空）
    = 本期淨利（損失）
    """
    # 取得毛利資料
    dash = get_dashboard_data(period=period)
    summary = dash["summary"]

    revenue = float(summary.get("revenue_total") or 0)
    cogs = float(summary.get("cogs_total") or 0)
    gross_profit = float(summary.get("gross_profit_total") or 0)
    gross_margin = summary.get("gross_margin_rate") or 0

    # 取得營業費用
    expense_data = get_expense_summary(year_month)
    total_opex = expense_data["total"]
    by_category = expense_data["by_category"]

    # 計算各層利益
    operating_income = gross_profit - total_opex
    operating_margin = operating_income / revenue if revenue else 0

    # 目前無其他收支資料，留空
    non_operating_income = 0.0
    non_operating_expense = 0.0
    pretax_income = operating_income + non_operating_income - non_operating_expense
    income_tax = 0.0
    net_income = pretax_income - income_tax
    net_margin = net_income / revenue if revenue else 0

    return {
        "period": period,
        "year_month": year_month,
        # 營業收入
        "revenue": round(revenue, 2),
        # 銷貨成本
        "cogs": round(cogs, 2),
        # 毛利
        "gross_profit": round(gross_profit, 2),
        "gross_margin_rate": round(gross_margin, 4),
        # 營業費用明細
        "opex_by_category": by_category,
        "total_opex": round(total_opex, 2),
        # 營業利益
        "operating_income": round(operating_income, 2),
        "operating_margin_rate": round(operating_margin, 4),
        # 營業外（目前留空）
        "non_operating_income": non_operating_income,
        "non_operating_expense": non_operating_expense,
        # 稅前淨利
        "pretax_income": round(pretax_income, 2),
        # 所得稅（目前留空）
        "income_tax": income_tax,
        # 本期淨利
        "net_income": round(net_income, 2),
        "net_margin_rate": round(net_margin, 4),
        # 客戶明細（供附註用）
        "by_customer": dash.get("by_customer", []),
        "by_item": dash.get("by_item", []),
        "trend": dash.get("trend", []),
    }