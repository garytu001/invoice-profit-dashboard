import streamlit as st
import pandas as pd
import sqlite3
import os
import plotly.express as px
import plotly.graph_objects as go

# 1. 核心邏輯整合 (直接從 main.py 匯入功能)
# 確保 main.py, db.py, profit_calculator.py 與此檔案同目錄
try:
    from main import (
        parse_invoice_with_gpt, 
        save_invoice_to_db, 
        get_dashboard,
        resolve_mime_type
    )
    from db import get_conn, init_db
except ImportError:
    st.error("請確認 main.py 與 db.py 已存在於專案資料夾中。")
    st.stop()

# 初始化資料庫
init_db()

# 2. 注入 HTML/CSS 原生視覺風格 (100% 複製 webapp.html 的色調與間距)
st.set_page_config(page_title="Invoice GP Dashboard", layout="wide")

st.markdown(f"""
    <style>
    :root {{
        --bg: #f5f8f4;
        --panel: #ffffff;
        --ink: #1b1e22;
        --muted: #5e6670;
        --line: #d8dde6;
        --brand: #0f766e;
        --brand-2: #2b6cb0;
        --warn: #b16a00;
    }}
    .stApp {{
        background: radial-gradient(1200px 500px at 10% -10%, #d8f2ec, transparent),
                    radial-gradient(1000px 500px at 100% 0%, #deecff, transparent),
                    var(--bg);
    }}
    /* 卡片與容器樣式 */
    .top-container {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 20px;
    }}
    h1 {{ color: var(--ink); font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
    .sub-text {{ color: var(--muted); font-size: 14px; margin-bottom: 16px; }}
    
    /* 按鈕樣式優化 */
    div.stButton > button {{
        background-color: var(--brand);
        color: white;
        border-radius: 10px;
        border: none;
        font-weight: 600;
    }}
    div.stButton > button:hover {{
        background-color: #0d645d;
        color: white;
    }}
    </style>
""", unsafe_allow_html=True)

# 3. 系統初始化與金鑰檢查
if "OPENAI_API_KEY" in st.secrets:
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
else:
    st.warning("⚠️ 檢測不到 OPENAI_API_KEY，請在 Streamlit Secrets 中設定。")

# 4. 頂部標題區 (對應 HTML .top)
st.markdown('<div class="top-container"><h1>億立可有限公司請款單影像解析與毛利網站</h1><div class="sub-text">Upload / Review / Cost / Dashboard / Export</div></div>', unsafe_allow_html=True)

# 5. 頁籤導覽 (對應 HTML .tabs)
tab_review, tab_cost, tab_dash, tab_export = st.tabs(["上傳與人工補正", "成本表管理", "報表儀表板", "匯出"])

# --- 頁籤 1：上傳與人工補正 ---
with tab_review:
    with st.container():
        c1, c2, c3 = st.columns([4, 1, 1])
        uploaded_file = c1.file_uploader("上傳影像 (JPG, PNG, WEBP)", type=["jpg", "png", "webp", "jpeg"], label_visibility="collapsed")
        
        if c2.button("解析預覽", use_container_width=True) and uploaded_file:
            with st.spinner("GPT 解析中..."):
                if c2.button("解析預覽", use_container_width=True) and uploaded_file:
                    with st.spinner("GPT 解析中..."):
                        content = uploaded_file.read()
                        
                        # --- 修正處：封裝一個相容的小物件 ---
                        class CompatibilityFile:
                            def __init__(self, st_file):
                                self.content_type = st_file.type
                        
                        compat_file = CompatibilityFile(uploaded_file)
                        # 傳入這個 compat_file，這樣你 main.py 裡的 file.content_type 就不會報錯了
                        mime_type = resolve_mime_type(compat_file, content)
                        # ----------------------------------
                        
                        try:
                            # 直接呼叫你 main.py 裡的函數
                            data = parse_invoice_with_gpt(content, mime_type=mime_type)
                            st.session_state['current_parsed'] = data
                            st.session_state['source_name'] = uploaded_file.name
                            st.success(f"解析完成：共 {len(data.get('items', []))} 筆明細")
                        except Exception as e:
                            st.error(f"解析失敗：{e}")
                data = parse_invoice_with_gpt(content, mime_type=mime_type)
                st.session_state['current_parsed'] = data
                st.session_state['source_name'] = uploaded_file.name

        if c3.button("確認入庫", use_container_width=True):
            if 'current_parsed' in st.session_state:
                inv_id = save_invoice_to_db(st.session_state['current_parsed'], st.session_state['source_name'])
                st.success(f"入庫成功：Invoice ID {inv_id}")
            else:
                st.error("請先進行解析預覽")

    if 'current_parsed' in st.session_state:
        p = st.session_state['current_parsed']
        st.markdown("### 單頭資訊")
        g1, g2, g3, g4 = st.columns(4)
        p['print_date'] = g1.text_input("印表日期", p.get('print_date', ''))
        p['period_start'] = g2.text_input("請款起", p.get('period_start', ''))
        p['period_end'] = g3.text_input("請款迄", p.get('period_end', ''))
        p['customer_name'] = g4.text_input("客戶名稱", p.get('customer_name', ''))

        st.markdown("### 明細紀錄")
        df_items = pd.DataFrame(p.get('items', []))
        # 使用原生 Data Editor 實現 HTML 的表格編輯功能
        edited_df = st.data_editor(df_items, num_rows="dynamic", use_container_width=True)
        st.session_state['current_parsed']['items'] = edited_df.to_dict('records')

# --- 頁籤 2：成本表管理 ---
with tab_cost:
    st.markdown("### 成本資料管理")
    conn = get_conn()
    df_c = pd.read_sql_query("SELECT * FROM cost_table", conn)
    st.data_editor(df_c, num_rows="dynamic", use_container_width=True, key="cost_editor")
    if st.button("儲存成本表變更"):
        # 這裡可實作將編輯後的 DataFrame 寫回 SQLite 的邏輯
        st.info("功能整合中")
    conn.close()

# --- 頁籤 3：報表儀表板 (對應 HTML .charts 與 .cards) ---
with tab_dash:
    p_col, b_col = st.columns([1, 4])
    period = p_col.selectbox("區間", ["month", "quarter", "year"], label_visibility="collapsed")
    
    if b_col.button("載入報表"):
        dash = get_dashboard(period=period)
        sum_data = dash['summary']
        
        # 摘要卡片 (100% 視覺對應 HTML .card)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("營收", f"${sum_data['revenue_total']:,.0f}")
        k2.metric("銷貨成本", f"${sum_data['cogs_total']:,.0f}")
        k3.metric("毛利", f"${sum_data['gross_profit_total']:,.0f}")
        k4.metric("毛利率", f"{(sum_data['gross_margin_rate'] or 0)*100:.1f}%")

        # 圖表區 (使用 Plotly 模擬 HTML 裡的客製化 SVG)
        st.markdown("### 圖表分析")
        g1, g2, g3 = st.columns(3)
        
        # 1. 客戶營收圓餅圖
        df_cust = pd.DataFrame(dash['by_customer'])
        fig_pie = px.pie(df_cust, values='revenue', names='customer_name', hole=.4,
                         color_discrete_sequence=px.colors.qualitative.Antique)
        fig_pie.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
        g1.plotly_chart(fig_pie, use_container_width=True)

        # 2. 客戶毛利長條圖
        fig_bar = px.bar(df_cust, x='customer_name', y='gross_profit',
                         color_discrete_sequence=['#0f766e'])
        fig_bar.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
        g2.plotly_chart(fig_bar, use_container_width=True)

        # 3. 趨勢折線圖
        df_trend = pd.DataFrame(dash['trend'])
        fig_line = px.line(df_trend, x='bucket', y=['revenue', 'gross_profit'],
                           color_discrete_map={'revenue': '#2b6cb0', 'gross_profit': '#0f766e'})
        fig_line.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
        g3.plotly_chart(fig_line, use_container_width=True)

# --- 頁籤 4：匯出 ---
with tab_export:
    st.markdown("### 數據匯出")
    conn = get_conn()
    all_items = pd.read_sql_query("SELECT * FROM invoice_items", conn)
    csv = all_items.to_csv(index=False).encode('utf-8-sig')
    st.download_button("下載完整明細 CSV", data=csv, file_name="invoice_export.csv", mime="text/csv")
    conn.close()
