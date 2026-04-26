import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path

# 設定為寬螢幕模式，給你的儀表板最大的顯示空間
st.set_page_config(page_title="請款單影像解析與毛利網站", layout="wide")

# 隱藏 Streamlit 預設的頂部空白與選單，讓畫面看起來就像純粹的網頁
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1rem; padding-bottom: 0;}
    </style>
""", unsafe_allow_html=True)

# 1. 提供一個輸入框，讓你可以隨時填入「真正大腦 (FastAPI)」的網址
st.markdown("### ⚙️ 系統連線設定")
api_url = st.text_input(
    "FastAPI 後端網址 (若在本地測試請維持預設；若已部署至 Render，請填入 Render 網址)", 
    value="http://127.0.0.1:8000"
)
st.markdown("---")

# 2. 讀取你寫好的原始 HTML 檔案
html_path = Path(__file__).parent / "webapp.html"

if html_path.exists():
    html_content = html_path.read_text(encoding="utf-8")
    
    # 3. 關鍵魔法：動態替換 JS 裡的 API_BASE 變數
    # 尋找你在 HTML 第 279 行寫的那段程式碼，把它換成畫面上輸入的 API 網址
    original_js = 'const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8000" : "";'
    injected_js = f'const API_BASE = "{api_url.rstrip("/")}";'
    
    # 進行替換 (這只會在記憶體中替換，不會去改動你的 GitHub 原始檔)
    html_content = html_content.replace(original_js, injected_js)
    
    # 4. 把你的 HTML 完整渲染出來 (設定高度為 1200px 確保圖表跟表格顯示完整)
    components.html(html_content, height=1200, scrolling=True)
else:
    st.error("找不到 `webapp.html` 檔案，請確認它與 `app.py` 放在同一個資料夾，並已上傳至 GitHub。")
