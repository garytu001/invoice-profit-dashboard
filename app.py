import os
import streamlit as st
import pandas as pd
import sqlite3

# 1. 讀取 Streamlit Secrets 的金鑰，並設定為環境變數
# 這樣你原本 main.py 裡面的 client = OpenAI() 就能自動抓到金鑰，完全不用改 main.py！
if "OPENAI_API_KEY" in st.secrets:
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

# 2. 匯入你原本寫好的強大核心邏輯 (完全不改動你的原有功能)
# 確保你的 main.py, db.py 等檔案都在同一個資料夾
from main import (
    parse_invoice_with_gpt, 
    save_invoice_to_db, 
    get_dashboard,
    resolve_mime_type
)
from db import get_conn, init_db

# 初始化資料庫
init_db()

# 3. 頁面風格設定 (保留你原有的深藍/專業風格)
st.set_page_config(page_title="Invoice GP Dashboard", layout="wide")
st.markdown("""
    <style>
    .stButton>button { background-color: #0f766e; color: white; font-weight: bold; }
    .stButton>button:hover { background-color: #0d645d; color: white; }
    </style>
""", unsafe_allow_html=True)

st.title("億立可有限公司請款單影像解析與毛利網站")
st.caption("Upload / Review / Cost / Dashboard / Export")

# 使用 Streamlit 原生的頁籤功能，完全對應你的 HTML tabs
tab1, tab2, tab3, tab4 = st.tabs(["上傳與人工補正", "成本表管理", "報表儀表板", "匯出"])

# ==========================================
# 頁籤 1：上傳與人工補正
# ==========================================
with tab1:
    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded_file = st.file_uploader("選擇請款單影像", type=['jpg','png','gif','webp'])
    
    # 預覽與入庫按鈕
    btn_col1, btn_col2 = st.columns([1, 8])
    if btn_col1.button("解析預覽", use_container_width=True):
        if uploaded_file is not None:
            with st.spinner("GPT 解析中..."):
                content = uploaded_file.read()
                mime_type = resolve_mime_type(uploaded_file, content)
                try:
                    # 直接呼叫你 main.py 裡的函數
                    parsed = parse_invoice_with_gpt(content, mime_type=mime_type)
                    st.session_state['parsed_data'] = parsed
                    st.session_state['source_filename'] = uploaded_file.name
                    st.success(f"解析完成：共 {len(parsed.get('items', []))} 筆明細")
                except Exception as e:
                    st.error(f"解析失敗：{e}")
        else:
            st.warning("請先上傳檔案")

    if btn_col2.button("確認入庫", type="secondary"):
        if 'parsed_data' in st.session_state:
            with st.spinner("資料入庫中..."):
                invoice_id = save_invoice_to_db(
                    st.session_state['parsed_data'], 
                    st.session_state.get('source_filename', 'manual')
                )
                st.success(f"入庫成功！Invoice ID: {invoice_id}")
                # 入庫後清空暫存
                del st.session_state['parsed_data']
        else:
            st.warning("請先進行解析預覽，確認資料後再入庫")

    # 顯示編輯區 (取代 HTML 的 input 欄位與 Table)
    if 'parsed_data' in st.session_state:
        st.subheader("單頭資訊")
        p_data = st.session_state['parsed_data']
        
        c1, c2, c3, c4 = st.columns(4)
        p_data['print_date'] = c1.text_input("印表日期", p_data.get('print_date', ''))
        p_data['period_start'] = c2.text_input("請款起", p_data.get('period_start', ''))
        p_data['period_end'] = c3.text_input("請款迄", p_data.get('period_end', ''))
        p_data['customer_name'] = c4.text_input("客戶名稱", p_data.get('customer_name', ''))

        st.subheader("明細資料 (可直接在表格內修改)")
        if p_data.get('items'):
            # 使用 Streamlit 超強的資料編輯器 (取代你原本寫的 <table> 和 <input>)
            df = pd.DataFrame(p_data['items'])
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
            # 將修改後的資料存回 session
            st.session_state['parsed_data']['items'] = edited_df.to_dict('records')

# ==========================================
# 頁籤 2：成本表管理
# ==========================================
with tab2:
    st.subheader("新增成本")
    with st.form("add_cost_form", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        product = c1.text_input("品名 (必填)")
        grade = c2.text_input("等級")
        spec = c3.text_input("規格")
        cost_per_unit = c4.number_input("成本單價", min_value=0.0, step=0.1)
        cost_unit = c5.selectbox("單位", ["才", "坪"])
        
        if st.form_submit_button("新增成本"):
            if product:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO cost_table (product, grade, spec, cost_per_unit, cost_unit) VALUES (?, ?, ?, ?, ?)",
                    (product, grade or None, spec or None, cost_per_unit, cost_unit)
                )
                conn.commit()
                conn.close()
                st.success("新增成功！")
            else:
                st.error("品名為必填！")

    st.subheader("現有成本表")
    conn = get_conn()
    df_costs = pd.read_sql_query("SELECT id, product, grade, spec, cost_per_unit, cost_unit, effective_from FROM cost_table", conn)
    conn.close()
    st.dataframe(df_costs, use_container_width=True)

# ==========================================
# 頁籤 3：報表儀表板
# ==========================================
with tab3:
    col1, col2 = st.columns([2, 8])
    period = col1.selectbox("統計區間", ["month", "quarter", "year"], format_func=lambda x: {"month":"月", "quarter":"季", "year":"年"}[x])
    
    if col2.button("載入報表"):
        with st.spinner("計算利潤中..."):
            dash_data = get_dashboard(period=period)
            
            # 1. 摘要卡片 (取代 HTML 的 .cards)
            st.markdown("### 彙總資訊")
            sc1, sc2, sc3, sc4 = st.columns(4)
            summary = dash_data['summary']
            sc1.metric("營收", f"${summary['revenue_total']:,.2f}")
            sc2.metric("銷貨成本", f"${summary['cogs_total']:,.2f}")
            sc3.metric("毛利", f"${summary['gross_profit_total']:,.2f}")
            margin = summary['gross_margin_rate']
            sc4.metric("毛利率", f"{margin*100:.2f}%" if margin else "--")

            st.markdown("---")
            
            # 2. 客戶與品項表格 (左右兩欄)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**客戶毛利排行**")
                st.dataframe(pd.DataFrame(dash_data['by_customer']), use_container_width=True)
            with c2:
                st.markdown("**品項毛利排行**")
                st.dataframe(pd.DataFrame(dash_data['by_item']), use_container_width=True)

            st.markdown("---")

            # 3. 圖表區 (取代 HTML 的 SVG 繪圖)
            st.markdown("### 圖表分析")
            chart_col1, chart_col2, chart_col3 = st.columns(3)
            
            # 將資料轉為 DataFrame 以利繪圖
            df_cust = pd.DataFrame(dash_data['by_customer'])
            df_trend = pd.DataFrame(dash_data['trend'])

            with chart_col1:
                st.markdown("**客戶營收占比**")
                if not df_cust.empty:
                    # Streamlit 無法直接畫圓餅圖，我們用 Altair 或 Bar chart 替代展示比例
                    st.bar_chart(df_cust.set_index('customer_name')['revenue'])
            
            with chart_col2:
                st.markdown("**客戶毛利排行**")
                if not df_cust.empty:
                    st.bar_chart(df_cust.set_index('customer_name')['gross_profit'])

            with chart_col3:
                st.markdown("**營收/毛利趨勢**")
                if not df_trend.empty:
                    st.line_chart(df_trend.set_index('bucket')[['revenue', 'gross_profit']])

# ==========================================
# 頁籤 4：匯出
# ==========================================
with tab4:
    st.markdown("### 資料匯出")
    st.info("匯出功能即將從資料庫提取最新資料。")
    
    conn = get_conn()
    df_items = pd.read_sql_query("SELECT * FROM invoice_items", conn)
    conn.close()

    csv = df_items.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="下載明細 CSV",
        data=csv,
        file_name='invoice_items.csv',
        mime='text/csv',
    )
