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
#損益表功能相關
import expenses_service as exp_svc
from income_statement import build_income_statement, EXPENSE_CATEGORIES


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

tab_review, tab_cost, tab_dash, tab_anomaly, tab_income, tab_export = st.tabs(
    ["上傳與人工補正", "成本表管理", "報表儀表板", "異常警示", "損益表", "匯出"]
)

# --- Tab 1: 上傳與人工補正 ---
with tab_review:
    c1, c2, c3 = st.columns([4, 1, 1])
    uploaded_files = c1.file_uploader(
        "Upload",
        type=["jpg", "png", "webp", "jpeg"],
        label_visibility="collapsed",
        accept_multiple_files=True,
        key="file_u"
    )

    if c2.button("解析預覽", use_container_width=True, key="btn_p"):
        if uploaded_files:
            st.session_state['parsed'] = []
            st.session_state['f_names'] = []
            progress = st.progress(0)
            status_text = st.empty()
            total = len(uploaded_files)

            # Calculate total size in bytes
            total_size = sum(len(f.getbuffer()) for f in uploaded_files)
            processed_size = 0

            for i, uploaded_file in enumerate(uploaded_files, start=1):
                with st.spinner(f"GPT 解析中：{uploaded_file.name}"):
                    uploaded_file.seek(0)
                    content = uploaded_file.read()
                    compat_f = CompatibilityFile(uploaded_file)
                    m_type = resolve_mime_type(compat_f, content)
                    try:
                        data = parse_invoice_with_gpt(content, mime_type=m_type)
                        st.session_state['parsed'].append(data)
                        st.session_state['f_names'].append(uploaded_file.name)
                    except Exception as e:
                        st.error(f"解析發生錯誤: {e}")

                processed_size += len(content)
                percent = int(processed_size / total_size * 100)
                progress.progress(percent)
                color = "🟩" if percent > 0.66 else "🟨" if percent > 0.33 else "🟥"
                status_text.text(f"進度：{percent}%（{i}/{total} 已完成）")

            st.success(f"✅ 已成功解析 {total} 張發票！")
            status_text.text("解析完成 🎉")


    if c3.button("確認入庫", use_container_width=True, key="btn_s"):
        if 'parsed' in st.session_state:
            try:
                for f_name, data in zip(st.session_state['f_names'], st.session_state['parsed']):
                    inv_id = svc.save_invoice_to_db(data, f_name)
                    st.success(f"✅ 成功入庫！{f_name} → 單號 ID: {inv_id}")
                del st.session_state['parsed']
            except Exception as e:
                st.error(f"入庫失敗: {e}")

    if 'parsed' in st.session_state:
        # Title and buttons side by side
        title_col, btn_collapse, btn_expand= st.columns([4, 1, 1])
        title_col.markdown("## 📦 已解析發票列表")

        # Initialize toggle state once
        if 'expand_all' not in st.session_state:
            st.session_state['expand_all'] = False

        # One button to toggle expand/collapse
        if btn_collapse.button("全部收合", use_container_width=True, key="collapse_btn"):
            st.session_state['expand_all'] = False
        if btn_expand.button("全部展開", use_container_width=True, key="expand_btn"):
            st.session_state['expand_all'] = True

        # Render each invoice with expander
        for idx, (f_name, p) in enumerate(zip(st.session_state['f_names'], st.session_state['parsed'])):
            with st.expander(f"📄 {f_name}", expanded=st.session_state['expand_all']):
                g1, g2, g3, g4 = st.columns(4)
                p['print_date'] = g1.text_input("印表日期", p.get('print_date', ''), key=f"print_date_{idx}")
                p['period_start'] = g2.text_input("請款起", p.get('period_start', ''), key=f"period_start_{idx}")
                p['period_end'] = g3.text_input("請款迄", p.get('period_end', ''), key=f"period_end_{idx}")
                p['customer_name'] = g4.text_input("客戶名稱", p.get('customer_name', ''), key=f"customer_name_{idx}")

                st.markdown("### 明細紀錄")
                df_items = pd.DataFrame(p.get('items', []))
                edited_df = st.data_editor(
                    df_items,
                    num_rows="dynamic",
                    use_container_width=True,
                    key=f"item_editor_{idx}"
                )
                p['items'] = edited_df.to_dict('records')

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
    
    st.markdown("---")
    st.markdown("#### ⚠️ 成本缺失警示")
    st.caption("以下品項在資料庫中找不到對應成本，請填入後點選儲存。")

    # 從 dashboard 取得缺失清單（用 month 最廣）
    try:
        _dash = get_dashboard_data(period="month")
        missing_items = _dash.get("missing_cost_items", [])
    except Exception as _e:
        missing_items = []
        st.error(f"無法載入缺失清單：{_e}")

    if not missing_items:
        st.success("✅ 所有品項均已有對應成本！")
    else:
        st.warning(f"共有 {len(missing_items)} 筆明細缺少成本資料")

        # 用來收集使用者填入的成本
        if "missing_cost_inputs" not in st.session_state:
            st.session_state["missing_cost_inputs"] = {}

        # 每個缺失品項顯示一列輸入欄
        for mi in missing_items:
            iid = mi["invoice_item_id"]
            label = f"{mi.get('product','')} {mi.get('grade','')} {mi.get('spec','')}".strip()
            key_cost  = f"mc_cost_{iid}"
            key_unit  = f"mc_unit_{iid}"
            key_saved = f"mc_saved_{iid}"

            cols = st.columns([3, 2, 1, 1])
            cols[0].write(f"🔸 {label}　｜　單號：{mi.get('order_no','')}　金額：{mi.get('amount','')}")
            fill_cost = cols[1].number_input("成本單價", min_value=0.0, key=key_cost)
            fill_unit = cols[2].selectbox("單位", ["才", "坪"], key=key_unit)

            if cols[3].button("儲存", key=f"mc_btn_{iid}"):
                if fill_cost > 0:
                    from models import ItemCostOverridePayload
                    svc.set_item_cost_override(
                        ItemCostOverridePayload(
                            invoice_item_id=iid,
                            cost_per_unit=fill_cost,
                            cost_unit=fill_unit,
                        )
                    )
                    st.session_state[key_saved] = True
                    st.success(f"✅ 已儲存：{label} → {fill_cost} / {fill_unit}")
                else:
                    st.warning("請輸入大於 0 的成本單價")

            if st.session_state.get(key_saved):
                st.caption("　　✅ 此筆已儲存")

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

#損益表TAB
with tab_income:
    st.markdown("### 綜合損益表")
    
    # --- 營業費用輸入 ---
    with st.expander("➕ 新增營業費用"):
        e1, e2, e3, e4, e5 = st.columns([2, 2, 2, 3, 1])
        exp_ym = e1.text_input("年月（如 2025-11）", key="exp_ym")
        exp_cat = e2.selectbox("費用類別", EXPENSE_CATEGORIES, key="exp_cat")
        exp_amt = e3.number_input("金額", min_value=0.0, key="exp_amt")
        exp_note = e4.text_input("備註（選填）", key="exp_note")
        if e5.button("新增", key="btn_add_exp"):
            if exp_ym and exp_amt > 0:
                exp_svc.save_expense(exp_ym, exp_cat, exp_amt, exp_note)
                st.success("✅ 費用已新增！")
            else:
                st.warning("請填入年月和金額")
    
    # --- 篩選條件 ---
    # --- 已輸入的營業費用清單（可修改） ---
    st.markdown("#### 📋 已輸入的營業費用")
    
    all_expenses = exp_svc.list_expenses()  # 不帶篩選，顯示全部
    
    if not all_expenses:
        st.info("尚未輸入任何營業費用，請使用上方「新增營業費用」。")
    else:
        # 整理成 DataFrame 顯示
        df_exp_list = pd.DataFrame(all_expenses)[["id", "year_month", "category", "amount", "note"]]
        df_exp_list.columns = ["ID", "年月", "類別", "金額", "備註"]
        
        # 用 data_editor 讓使用者直接在表格內修改
        edited_exp = st.data_editor(
            df_exp_list,
            use_container_width=True,
            num_rows="fixed",          # 不讓用戶在這裡新增列，新增走上面的 expander
            disabled=["ID"],           # ID 欄不可編輯
            key="exp_editor",
            column_config={
                "金額": st.column_config.NumberColumn("金額", min_value=0, format="%.0f"),
                "年月": st.column_config.TextColumn("年月", help="格式：2025-11"),
            }
        )
        
        # 儲存修改按鈕
        save_col, del_col = st.columns([2, 3])
        
        if save_col.button("💾 儲存費用修改", key="btn_save_exp_edits"):
            conn = get_conn()
            cur = conn.cursor()
            changed = 0
            for _, row_e in edited_exp.iterrows():
                # 逐列比對原始值，只更新有變動的
                orig = next((e for e in all_expenses if e["id"] == row_e["ID"]), None)
                if orig is None:
                    continue
                new_ym  = str(row_e["年月"]).strip()
                new_cat = str(row_e["類別"]).strip()
                new_amt = float(row_e["金額"])
                new_note= str(row_e["備註"] or "").strip()
                if (new_ym  != orig["year_month"] or
                    new_cat != orig["category"]   or
                    new_amt != float(orig["amount"]) or
                    new_note!= (orig["note"] or "")):
                    cur.execute(
                        """UPDATE operating_expenses
                           SET year_month=?, category=?, amount=?, note=?
                           WHERE id=?""",
                        (new_ym, new_cat, new_amt, new_note, int(row_e["ID"]))
                    )
                    changed += 1
            conn.commit()
            conn.close()
            if changed:
                st.success(f"✅ 已更新 {changed} 筆費用！")
            else:
                st.info("沒有偵測到修改。")
        
        # 刪除功能：輸入 ID 刪除
        with del_col.expander("🗑️ 刪除某筆費用"):
            del_id = st.number_input("輸入要刪除的 ID", min_value=1, step=1, key="del_exp_id")
            if st.button("確認刪除", key="btn_del_exp"):
                ok = exp_svc.delete_expense(int(del_id))
                if ok:
                    st.success(f"✅ 已刪除 ID={del_id}")
                else:
                    st.error(f"找不到 ID={del_id}")
    
    st.markdown("---")
    
    f1, f2, f3 = st.columns([2, 2, 2])
    sel_period_is = f1.selectbox(
        "彙整區間", ["month", "quarter", "year"],
        key="is_period"
    )
    sel_ym_filter = f2.text_input(
        "費用篩選年月（選填，如 2025-11）",
        key="is_ym"
    )
    
    if f3.button("產生損益表", key="btn_gen_is"):
        try:
            st.session_state["income_stmt"] = build_income_statement(
                period=sel_period_is,
                year_month=sel_ym_filter or None
            )
        except Exception as e:
            st.error(f"產生失敗：{e}")
    
    # --- 顯示損益表 ---
    if "income_stmt" in st.session_state:
        IS = st.session_state["income_stmt"]
        
        st.markdown("---")
        st.markdown("#### 綜合損益表（Comprehensive Income Statement）")
        
        # 用表格呈現，仿正式財報格式
        def fmt(val):
            """格式化數字，負數用括號表示（台灣財報慣例）"""
            if val is None:
                return "—"
            if val < 0:
                return f"({abs(val):,.0f})"
            return f"{val:,.0f}"
        
        def pct(val):
            if val is None:
                return "—"
            return f"{val*100:.1f}%"
        
        # 損益表主體
        rows = [
            ("營業收入淨額", IS["revenue"], pct(1.0), ""),
            ("減：銷貨成本", -IS["cogs"], pct(-IS["cogs"]/IS["revenue"] if IS["revenue"] else 0), ""),
            ("　毛利（毛損）", IS["gross_profit"], pct(IS["gross_margin_rate"]), "bold"),
            ("", None, "", ""),
            ("營業費用", None, "", "header"),
        ]
        
        # 費用明細
        for cat, amt in IS["opex_by_category"].items():
            rows.append((f"　{cat}", -amt, pct(-amt/IS["revenue"] if IS["revenue"] else 0), ""))
        
        rows += [
            ("　營業費用合計", -IS["total_opex"], pct(-IS["total_opex"]/IS["revenue"] if IS["revenue"] else 0), ""),
            ("　營業利益（損失）", IS["operating_income"], pct(IS["operating_margin_rate"]), "bold"),
            ("", None, "", ""),
            ("營業外收入及支出", None, "", "header"),
            ("　其他收入", IS["non_operating_income"], "—", ""),
            ("　其他支出", -IS["non_operating_expense"], "—", ""),
            ("　稅前淨利（損失）", IS["pretax_income"], pct(IS["net_margin_rate"]), "bold"),
            ("　所得稅費用", -IS["income_tax"], "—", ""),
            ("本期淨利（損失）", IS["net_income"], pct(IS["net_margin_rate"]), "bold"),
        ]
        
        # 渲染表格
        for label, val, pct_val, style in rows:
            if not label:
                st.markdown("&nbsp;", unsafe_allow_html=True)
                continue
            if style == "header":
                st.markdown(f"**{label}**")
                continue
            
            col1, col2, col3 = st.columns([5, 2, 2])
            if style == "bold":
                col1.markdown(f"**{label}**")
                col2.markdown(f"**{fmt(val)}**")
                col3.markdown(f"**{pct_val}**")
            else:
                col1.write(label)
                col2.write(fmt(val))
                col3.write(pct_val)
        
        st.markdown("---")
        
        # KPI 卡片
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("營業收入", f"${IS['revenue']:,.0f}")
        k2.metric("毛利率", pct(IS["gross_margin_rate"]))
        k3.metric("營業利益率", pct(IS["operating_margin_rate"]))
        k4.metric("淨利率", pct(IS["net_margin_rate"]))
        
        # 費用結構圖
        if IS["opex_by_category"]:
            import plotly.express as px
            df_exp = pd.DataFrame([
                {"類別": k, "金額": v}
                for k, v in IS["opex_by_category"].items()
            ])
            fig_exp = px.pie(
                df_exp, values="金額", names="類別",
                title="營業費用結構",
                hole=0.4
            )
            st.plotly_chart(fig_exp, use_container_width=True)
        
        # 附註建議
        st.markdown("---")
        st.markdown("#### 附註揭露（Notes）")
        st.markdown("**附註一：重要會計政策**")
        st.info("本報表採用標準成本法估算銷貨成本，以請款單金額作為營業收入基礎。單位不一致或缺少成本資料之明細不計入成本計算。")
        
        st.markdown("**附註二：客戶營收集中度**")
        if IS.get("by_customer"):
            top = sorted(IS["by_customer"], key=lambda x: x["revenue"], reverse=True)[:3]
            for i, c in enumerate(top, 1):
                pct_rev = c["revenue"] / IS["revenue"] * 100 if IS["revenue"] else 0
                st.write(f"{i}. {c['customer_name']}：營收 ${c['revenue']:,.0f}（佔比 {pct_rev:.1f}%）")
        
        st.markdown("**附註三：主要商品**")
        if IS.get("by_item"):
            top_items = sorted(IS["by_item"], key=lambda x: x["revenue"], reverse=True)[:5]
            for item in top_items:
                st.write(f"- {item['item_key']}：${item['revenue']:,.0f}")
        
        # 在損益表顯示區塊最下面加
        st.markdown("---")
        st.markdown("#### 匯出報表")
        from export_income_statement import (
            export_income_statement_excel,
            export_income_statement_ppt
        )

        dl1, dl2 = st.columns(2)

        excel_bytes = export_income_statement_excel(IS)
        dl1.download_button(
            label="📊 下載 Excel 損益表",
            data=excel_bytes,
            file_name=f"損益表_{IS.get('year_month') or IS.get('period')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_dl_excel"
        )

        ppt_bytes = export_income_statement_ppt(IS)
        dl2.download_button(
            label="📑 下載 PPT 損益表",
            data=ppt_bytes,
            file_name=f"損益表_{IS.get('year_month') or IS.get('period')}.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            key="btn_dl_ppt"
        )

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
    # Title and buttons side by side
    title_col, btn_refresh, btn_collapse, btn_expand= st.columns([4, 1, 1, 1])
    title_col.markdown("### 🔴 財務異常警示")
    st.caption("系統自動偵測單價、金額偏離歷史均值的可疑交易，請人工確認。")

    # 初始化展開/收合狀態
    if 'expand_anomaly' not in st.session_state:
        st.session_state['expand_anomaly'] = False

    # Buttons next to the title
    if btn_refresh.button("重新整理", use_container_width=True, key="refresh_anomaly_btn"):
        st.session_state['anomalies'] = get_anomalies()
        st.success("✅ 異常資料已重新整理！")
    if btn_collapse.button("全部收合", use_container_width=True, key="collapse_anomaly_btn"):
        st.session_state['expand_anomaly'] = False
    if btn_expand.button("全部展開", use_container_width=True, key="expand_anomaly_btn"):
        st.session_state['expand_anomaly'] = True
    
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


