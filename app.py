import streamlit as st
import pandas as pd
import sqlite3
import os
import plotly.express as px

# --- 1. 核心邏輯整合 ---
from invoice_parser import parse_invoice_with_gpt
from image_utils import resolve_mime_type
from db import get_conn, init_db
from reports import get_dashboard_data
# 導入整個 service 模組
import invoice_service as svc
from reports import get_anomalies

# 初始化資料庫
init_db()

# --- 2. API Key 設定 ---
if "OPENAI_API_KEY" in st.secrets:
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
else:
    st.error("❌ 找不到 OpenAI API 金鑰，請在 Streamlit 管理後台設定 Secrets。")
    st.stop()

# 3. 視覺風格設定 (維持不變)
st.set_page_config(page_title="Invoice GP Dashboard", layout="wide")
st.markdown("""
    <style>
    :root { --bg: #f5f8f4; --panel: #ffffff; --brand: #0f766e; }
    .stApp { background: var(--bg); }
    .top-container { background: var(--panel); border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    div.stButton > button { background-color: var(--brand); color: white; border-radius: 8px; font-weight: 600; }
    </style>
""", unsafe_allow_html=True)

# 4. 頂部標題
st.markdown('<div class="top-container"><h1>億立可有限公司請款單影像解析與毛利網站</h1><p>Upload / Review / Cost / Dashboard</p></div>', unsafe_allow_html=True)

class CompatibilityFile:
    def __init__(self, st_file):
        self.content_type = st_file.type

tab_review, tab_cost, tab_dash, tab_anomaly, tab_export = st.tabs(
    ["上傳與人工補正", "成本表管理", "報表儀表板", "異常警示", "匯出"]
)

# --- Tab 1: 上傳與人工補正 ---
with tab_review:
    c1, c2, c3 = st.columns([4, 1, 1])
    uploaded_file = c1.file_uploader("Upload", type=["jpg", "png", "webp", "jpeg"], label_visibility="collapsed", key="file_u")
    
    if c2.button("解析預覽", use_container_width=True, key="btn_p"):
        if uploaded_file:
            with st.spinner("GPT 解析中..."):
                uploaded_file.seek(0)
                content = uploaded_file.read()
                compat_f = CompatibilityFile(uploaded_file)
                m_type = resolve_mime_type(compat_f, content)
                try:
                    data = parse_invoice_with_gpt(content, mime_type=m_type)
                    st.session_state['parsed'] = data
                    st.session_state['f_name'] = str(uploaded_file.name)
                except Exception as e:
                    st.error(f"解析發生錯誤: {e}")

    if c3.button("確認入庫", use_container_width=True, key="btn_s"):
        if 'parsed' in st.session_state:
            try:
                # 這裡確保呼叫的是 svc 模組內的 save_invoice_to_db
                # 如果報錯是在 invoice_service.py 裡面發生，代表那邊也需要修正
                inv_id = svc.save_invoice_to_db(st.session_state['parsed'], st.session_state['f_name'])
                st.success(f"✅ 成功入庫！單號 ID: {inv_id}")
                del st.session_state['parsed']
            except NameError as ne:
                st.error(f"程式邏輯錯誤: {ne}。請檢查 invoice_service.py 內部的函數呼叫。")
            except Exception as e:
                st.error(f"入庫失敗: {e}")

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
        edited_df = st.data_editor(df_items, num_rows="dynamic", use_container_width=True, key="item_editor")
        st.session_state['parsed']['items'] = edited_df.to_dict('records')

# --- Tab 2: 成本表管理 ---
with tab_cost:
    st.markdown("### 成本表管理")
    
    # --- 上傳 CSV ---
    st.markdown("#### 批次匯入 CSV")
    uploaded_csv = st.file_uploader(
        "上傳成本表 CSV（需包含欄位：product, cost_per_unit, cost_unit）",
        type=["csv"],
        key="cost_csv_upload"
    )
    if uploaded_csv and st.button("匯入 CSV", key="btn_import_csv"):
        content = uploaded_csv.read()
        result = svc.import_costs_csv(content)
        st.success(f"✅ 成功匯入 {result['inserted']} 筆成本資料！")
    
    st.markdown("---")
    
    # --- 手動新增單筆 ---
    st.markdown("#### 手動新增單筆成本")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    new_product = c1.text_input("品名", key="new_product")
    new_grade = c2.text_input("等級", key="new_grade")
    new_spec = c3.text_input("規格", key="new_spec")
    new_cost = c4.number_input("成本", min_value=0.0, key="new_cost")
    new_unit = c5.selectbox("單位", ["才", "坪"], key="new_unit")
    new_date = c6.text_input("生效日（選填）", key="new_date")
    
    if st.button("新增這筆成本", key="btn_add_cost"):
        if new_product:
            from models import CostRowPayload
            payload = CostRowPayload(
                product=new_product,
                grade=new_grade or None,
                spec=new_spec or None,
                cost_per_unit=new_cost,
                cost_unit=new_unit,
                effective_from=new_date or None,
            )
            svc.create_cost(payload)
            st.success(f"✅ 已新增：{new_product} {new_grade} {new_spec}")
        else:
            st.warning("請至少填入品名！")
    
    st.markdown("---")
    
    # --- 顯示目前成本表 ---
    st.markdown("#### 目前成本表")
    conn = get_conn()
    df_cost = pd.read_sql_query("SELECT * FROM cost_table", conn)
    conn.close()
    st.dataframe(df_cost, use_container_width=True)

# --- Tab 3: 報表儀表板 ---
with tab_dash:
    p_col, b_col = st.columns([1, 5])
    sel_period = p_col.selectbox("區間", ["month", "quarter", "year"], label_visibility="collapsed")
    
    # 點擊按鈕後，將結果存入 session_state 以免畫面刷新時消失
    if b_col.button("載入最新報表", key="btn_load_dash"):
        try:
            # 修正點：必須與 import 的名稱 get_dashboard_data 一致
            st.session_state['dash_result'] = get_dashboard_data(period=sel_period)
        except Exception as e:
            st.error(f"報表計算出錯: {e}")

    # 如果有報表資料，就顯示出來
    if 'dash_result' in st.session_state:
        dash = st.session_state['dash_result']
        sum_d = dash['summary']
        
        # 摘要卡片
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("總營收", f"${sum_d['revenue_total']:,.0f}")
        k2.metric("總成本", f"${sum_d['cogs_total']:,.0f}")
        k3.metric("總毛利", f"${sum_d['gross_profit_total']:,.0f}")
        k4.metric("毛利率", f"{(sum_d['gross_margin_rate'] or 0)*100:.1f}%")

        st.markdown("---")
        
        # 檢查是否有明細資料可以繪圖
        if not dash['by_customer'] or len(dash['by_customer']) == 0:
            st.warning("⚠️ 雖然有總營收，但目前區間內沒有可用的客戶明細或趨勢資料。請檢查：\n1. 資料庫的 item_date 格式是否為 YYYY-MM-DD\n2. 選擇的區間是否正確")
        else:
            g1, g2, g3 = st.columns(3)
            
            df_cust = pd.DataFrame(dash['by_customer'])
            # 1. 客戶營收圓餅
            fig1 = px.pie(df_cust, values='revenue', names='customer_name', hole=.4, title="客戶營收佔比")
            g1.plotly_chart(fig1, use_container_width=True)

            # 2. 客戶毛利長條
            fig2 = px.bar(df_cust, x='customer_name', y='gross_profit', title="各客戶毛利貢獻", color_discrete_sequence=['#0f766e'])
            g2.plotly_chart(fig2, use_container_width=True)

            # 3. 趨勢折線(#新增功能:營收趨勢預測)
            if dash.get('trend'):
                df_trend = pd.DataFrame(dash['trend'])
    
                # 計算移動平均預測
                revenues = df_trend['revenue'].tolist()
                if len(revenues) >= 3:
                    predicted = sum(revenues[-3:]) / 3
                    upper = predicted * 1.2
                    lower = predicted * 0.8
                    
                    # 加入預測點
                    last_bucket = df_trend['bucket'].iloc[-1]
                    # 產生下一個期間的標籤
                    try:
                        parts = last_bucket.split('-')
                        next_m = int(parts[1]) + 1
                        next_y = int(parts[0])
                        if next_m > 12:
                            next_m = 1
                            next_y += 1
                        next_bucket = f"{next_y}-{next_m:02d} (預測)"
                    except:
                        next_bucket = "下期 (預測)"
                    
                    import plotly.graph_objects as go
                    fig3 = go.Figure()
                    
                    # 歷史實線
                    fig3.add_trace(go.Scatter(
                        x=df_trend['bucket'], y=df_trend['revenue'],
                        mode='lines+markers', name='實際營收', line=dict(color='#0f766e')
                    ))
                    # 預測點虛線
                    fig3.add_trace(go.Scatter(
                        x=[df_trend['bucket'].iloc[-1], next_bucket],
                        y=[revenues[-1], predicted],
                        mode='lines+markers', name='預測營收',
                        line=dict(color='#f59e0b', dash='dash')
                    ))
                    # 預測區間
                    fig3.add_trace(go.Scatter(
                        x=[next_bucket, next_bucket],
                        y=[lower, upper],
                        mode='markers', name=f'預測區間 ({lower:,.0f}~{upper:,.0f})',
                        marker=dict(color='#f59e0b', size=8, symbol='line-ew')
                    ))
                    fig3.update_layout(title="營收趨勢（含下期預測）", showlegend=True)
                    g3.plotly_chart(fig3, use_container_width=True)
                    g3.caption(f"預測下期營收：{predicted:,.0f}（±20% 區間：{lower:,.0f} ~ {upper:,.0f}）")
                else:
                    fig3 = px.line(df_trend, x='bucket', y=['revenue', 'gross_profit'], title="營收/毛利趨勢（資料不足3期，無法預測）")
                    g3.plotly_chart(fig3, use_container_width=True)
                
            # 客戶評分區塊
            st.markdown("---")
            st.markdown("### 客戶價值評分")
            from reports import get_customer_scores
            df_scores = pd.DataFrame(get_customer_scores())
            if not df_scores.empty:
            # 泡泡圖
                fig_bubble = px.scatter(
                df_scores,
                x="transaction_count", y="gross_margin_rate",
                size="revenue", color="tier",
                text="customer_name",
                title="客戶價值泡泡圖（X=交易次數、Y=毛利率%、泡泡大小=營收）",
                labels={"transaction_count":"交易次數", "gross_margin_rate":"毛利率(%)"},
                color_discrete_map={"⭐ VIP":"#0f766e", "一般":"#f59e0b", "低度往來":"#ef4444"}
            )
            st.plotly_chart(fig_bubble, use_container_width=True)
            
            # 評分表格
            st.dataframe(
                df_scores[["customer_name","tier","score","revenue","gross_profit","gross_margin_rate","transaction_count"]],
                use_container_width=True
            )
            
            #以下是商品價值評分的新程式碼
            st.markdown("---")
            st.markdown("### 商品組合分析（BCG 矩陣）")
            if 'dash_result' in st.session_state:
                df_items = pd.DataFrame(st.session_state['dash_result']['by_item'])
                if not df_items.empty:
                    # 計算中位數作為高低分界
                    median_rev = df_items['revenue'].median()
                    median_margin = (df_items['gross_profit'] / df_items['revenue'].replace(0,1) * 100).median()
                    
                    df_items['gross_margin_pct'] = df_items['gross_profit'] / df_items['revenue'].replace(0,1) * 100
                    
                    def classify(row):
                        high_rev = row['revenue'] >= median_rev
                        high_margin = row['gross_margin_pct'] >= median_margin
                        if high_rev and high_margin:
                            return '⭐ 明星'
                        elif high_rev and not high_margin:
                            return '🐄 金牛'
                        elif not high_rev and high_margin:
                            return '❓ 問題'
                        else:
                            return '🐕 落水狗'
                    
                    df_items['category'] = df_items.apply(classify, axis=1)
                    
                    fig_bcg = px.scatter(
                        df_items,
                        x='revenue', y='gross_margin_pct',
                        color='category', text='item_key',
                        title='商品組合分析（X=營收、Y=毛利率%）',
                        labels={'revenue':'營收', 'gross_margin_pct':'毛利率(%)'},
                        color_discrete_map={
                            '⭐ 明星':'#0f766e',
                            '🐄 金牛':'#f59e0b',
                            '❓ 問題':'#3b82f6',
                            '🐕 落水狗':'#ef4444'
                        }
                    )
                    # 加上分界線
                    fig_bcg.add_hline(y=median_margin, line_dash="dash", line_color="gray", annotation_text="毛利率中位線")
                    fig_bcg.add_vline(x=median_rev, line_dash="dash", line_color="gray", annotation_text="營收中位線")
                    fig_bcg.update_traces(textposition='top center')
                    st.plotly_chart(fig_bcg, use_container_width=True)
                    
                    # 說明
                    c1, c2, c3, c4 = st.columns(4)
                    c1.info("⭐ **明星**\n高營收高毛利\n主力商品")
                    c2.warning("🐄 **金牛**\n高營收低毛利\n走量但不賺")
                    c3.info("❓ **問題**\n低營收高毛利\n有潛力待推廣")
                    c4.error("🐕 **落水狗**\n低營收低毛利\n考慮停賣")

# --- Tab 4: 匯出 ---
with tab_export:
    st.markdown("### 下載原始數據")
    conn = get_conn()
    raw_df = pd.read_sql_query("SELECT * FROM invoice_items", conn)
    conn.close()
    
    csv_data = raw_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("下載請款明細 (CSV)", data=csv_data, file_name="export.csv", key="btn_download")


#這也是異常警示新增的部分#
with tab_anomaly:
    st.markdown("### 🔴 財務異常警示")
    st.caption("系統自動偵測單價、金額偏離歷史均值的可疑交易，請人工確認。")
    
    if st.button("重新偵測異常", key="btn_anomaly"):
        st.session_state["anomalies"] = get_anomalies()
    
    if "anomalies" not in st.session_state:
        st.session_state["anomalies"] = get_anomalies()
    
    anomalies = st.session_state["anomalies"]
    
    if not anomalies:
        st.success("✅ 目前沒有偵測到異常交易！")
    else:
        st.warning(f"⚠️ 共偵測到 {len(anomalies)} 筆可疑交易")
        for a in anomalies:
            with st.expander(
                f"🔴 {a['customer_name']} | {a['item_date']} | "
                f"{a['product']} {a['grade']} | 金額 {a['amount']:,.0f}"
            ):
                for reason in a["reasons"]:
                    st.error(f"⚠️ {reason}")
                st.write(f"單號：{a['order_no']}　規格：{a['spec']}　單價：{a['unit_price']}")
