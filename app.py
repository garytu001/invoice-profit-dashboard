import streamlit as st
import pandas as pd
import sqlite3
import os
import plotly.express as px

# 1. 核心邏輯整合 (導入你原本 main.py 與 db.py 的功能)
from main import (
    parse_invoice_with_gpt, 
    save_invoice_to_db, 
    get_dashboard,
    resolve_mime_type
)
from db import get_conn, init_db

# 初始化資料庫
init_db()

# 2. 注入 HTML/CSS 原生視覺風格 (100% 複製 webapp.html 的設計)
st.set_page_config(page_title="Invoice GP Dashboard", layout="wide")

st.markdown("""
    <style>
    :root {
        --bg: #f5f8f4;
        --panel: #ffffff;
        --ink: #1b1e22;
        --muted: #5e6670;
        --line: #d8dde6;
        --brand: #0f766e;
        --brand-2: #2b6cb0;
    }
    /* 100% 還原你 HTML 裡的背景漸層 */
    .stApp {
        background: radial-gradient(1200px 500px at 10% -10%, #d8f2ec, transparent),
                    radial-gradient(1000px 500px at 100% 0%, #deecff, transparent),
                    var(--bg);
    }
    /* 頂部標題區塊卡片 */
    .top-container {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
    }
    .top-container h1 { color: var(--ink); font-size: 28px; font-weight: 700; margin: 0; }
    .top-container p { color: var(--muted); font-size: 14px; margin-top: 8px; }

    /* 按鈕樣式優化：符合你原本的深藍色設計 */
    div.stButton > button {
        background-color: var(--brand);
        color: white;
        border-radius: 8px;
        border: none;
        padding: 0.5rem 1rem;
        font-weight: 600;
        transition: all 0.2s;
    }
    div.stButton > button:hover {
        background-color: #0d645d;
        color: white;
        transform: translateY(-1px);
    }
    /* 調整 Tabs 樣式使其更接近 HTML 設計 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
        background-color: transparent;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        background-color: transparent;
        border: none;
        color: var(--muted);
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        color: var(--brand) !important;
        border-bottom: 2px solid var(--brand) !important;
    }
    </style>
""", unsafe_allow_html=True)

# 3. 系統初始化與金鑰檢查
if "OPENAI_API_KEY" in st.secrets:
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
else:
    st.error("❌ 找不到 OpenAI API 金鑰，請在 Streamlit 管理後台設定 Secrets。")
    st.stop()

# 4. 頂部標題區
st.markdown("""
    <div class="top-container">
        <h1>億立可有限公司請款單影像解析與毛利網站</h1>
        <p>Upload / Review / Cost / Dashboard / Export</p>
    </div>
""", unsafe_allow_html=True)

# 5. 定義相容性類別 (解決 resolve_mime_type 報錯)
class CompatibilityFile:
    def __init__(self, st_file):
        self.content_type = st_file.type

# 6. 頁籤導覽 (對應 webapp.html 的四個 Tab)
tab_review, tab_cost, tab_dash, tab_export = st.tabs(["上傳與人工補正", "成本表管理", "報表儀表板", "匯出"])

# --- Tab 1: 上傳與人工補正 ---
with tab_review:
    c1, c2, c3 = st.columns([4, 1, 1])
    uploaded_file = c1.file_uploader("Upload", type=["jpg", "png", "webp", "jpeg"], label_visibility="collapsed", key="file_u")
    
    if c2.button("解析預覽", use_container_width=True, key="btn_p"):
        if uploaded_file:
            with st.spinner("GPT 解析中..."):
                content = uploaded_file.read()
                # 封裝成相容物件，滿足 main.py 邏輯
                compat_f = CompatibilityFile(uploaded_file)
                m_type = resolve_mime_type(compat_f, content)
                try:
                    data = parse_invoice_with_gpt(content, mime_type=m_type)
                    st.session_state['parsed'] = data
                    st.session_state['f_name'] = uploaded_file.name
                except Exception as e:
                    st.error(f"解析發生錯誤: {e}")
        else:
            st.warning("請先選擇檔案")

    if c3.button("確認入庫", use_container_width=True, key="btn_s"):
        if 'parsed' in st.session_state:
            inv_id = save_invoice_to_db(st.session_state['parsed'], st.session_state['f_name'])
            st.success(f"✅ 成功入庫！單號 ID: {inv_id}")
            del st.session_state['parsed']
        else:
            st.error("請先進行解析預覽")

    # 顯示編輯區域 (完全對應你的 HTML Input 欄位)
    if 'parsed' in st.session_state:
        p = st.session_state['parsed']
        st.markdown("### 單頭資訊")
        g1, g2, g3, g4 = st.columns(4)
        p['print_date'] = g1.text_input("印表日期", p.get('print_date', ''))
        p['period_start'] = g2.text_input("請款起", p.get('period_start', ''))
        p['period_end'] = g3.text_input("請款迄", p.get('period_end', ''))
        p['customer_name'] = g4.text_input("客戶名稱", p.get('customer_name', ''))

        st.markdown("### 明細紀錄")
        df_items = pd.DataFrame(p.get('items', []))
        # 使用 Data Editor 模擬 HTML 表格修改
        edited_df = st.data_editor(df_items, num_rows="dynamic", use_container_width=True, key="item_editor")
        st.session_state['parsed']['items'] = edited_df.to_dict('records')

# --- Tab 2: 成本表管理 ---
with tab_cost:
    st.markdown("### 成本資料庫")
    conn = get_conn()
    df_cost = pd.read_sql_query("SELECT * FROM cost_table", conn)
    conn.close()
    st.data_editor(df_cost, num_rows="dynamic", use_container_width=True, key="cost_edit_table")
    st.button("保存變更", key="btn_save_cost")

# --- Tab 3: 報表儀表板 (模擬 HTML SVG 圖表) ---
with tab_dash:
    p_col, b_col = st.columns([1, 5])
    sel_period = p_col.selectbox("區間", ["month", "quarter", "year"], label_visibility="collapsed")
    
    if b_col.button("載入最新報表", key="btn_load_dash"):
        dash = get_dashboard(period=sel_period)
        sum_d = dash['summary']
        
        # 摘要卡片
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("總營收", f"${sum_d['revenue_total']:,.0f}")
        k2.metric("總成本", f"${sum_d['cogs_total']:,.0f}")
        k3.metric("總毛利", f"${sum_d['gross_profit_total']:,.0f}")
        k4.metric("毛利率", f"{(sum_d['gross_margin_rate'] or 0)*100:.1f}%")

        # 使用 Plotly 1:1 還原你 HTML 裡的圖表佈局
        st.markdown("---")
        g1, g2, g3 = st.columns(3)
        
        df_cust = pd.DataFrame(dash['by_customer'])
        # 1. 客戶營收圓餅 (對應左側 Pie Chart)
        fig1 = px.pie(df_cust, values='revenue', names='customer_name', hole=.4, title="客戶營收佔比")
        g1.plotly_chart(fig1, use_container_width=True)

        # 2. 客戶毛利長條 (對應中間 Bar Chart)
        fig2 = px.bar(df_cust, x='customer_name', y='gross_profit', title="各客戶毛利貢獻", color_discrete_sequence=['#0f766e'])
        g2.plotly_chart(fig2, use_container_width=True)

        # 3. 趨勢折線 (對應右側 Line Chart)
        df_trend = pd.DataFrame(dash['trend'])
        fig3 = px.line(df_trend, x='bucket', y=['revenue', 'gross_profit'], title="營收/毛利趨勢")
        g3.plotly_chart(fig3, use_container_width=True)

# --- Tab 4: 匯出 ---
with tab_export:
    st.markdown("### 下載原始數據")
    conn = get_conn()
    raw_df = pd.read_sql_query("SELECT * FROM invoice_items", conn)
    conn.close()
    
    csv_data = raw_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("下載請款明細 (CSV)", data=csv_data, file_name="export.csv", key="btn_download")
