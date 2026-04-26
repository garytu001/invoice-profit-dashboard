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
            # 使用
