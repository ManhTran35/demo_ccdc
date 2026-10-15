"""Viettel Post inspired presentation, with no changes to business rules."""
from html import escape

import streamlit as st

from core import format_date, AGE_GROUPS

RED = "#E51937"

CSS = """
<style>
:root { --vtp-red:#E51937; --vtp-ink:#202632; --vtp-muted:#737B8C; }
.stApp { background:#F6F7F9; color:var(--vtp-ink); }
[data-testid="stHeader"] { background:rgba(246,247,249,.96); }
[data-testid="stMainBlockContainer"] { max-width:1580px; padding:2.2rem 2.6rem 3rem; }
h1,h2,h3 { color:var(--vtp-ink); letter-spacing:-.035em; }
h1 { font-size:2rem !important; font-weight:750 !important; }
h2 { font-size:1.4rem !important; }
h3 { font-size:1.12rem !important; letter-spacing:-.02em; }
[data-testid="stSidebar"] { background:#fff; border-right:1px solid #E8EBF0; }
[data-testid="stSidebarUserContent"] { padding-top:1.65rem; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.85rem; }
.vtp-brand { padding:0 0 1.45rem; border-bottom:1px solid #EBEDF1; margin-bottom:.25rem; }
.vtp-wordmark { font:italic 900 29px Arial,sans-serif; letter-spacing:-1.6px; color:#E51937; }
.vtp-wordmark span { color:#E51937; }
.vtp-brand small { display:block; color:#8B92A1; font-size:10px; font-weight:700; letter-spacing:2.3px; margin-top:6px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label p { font-size:14px; }
[data-testid="stSidebar"] [role="radiogroup"] { gap:5px; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding:9px 11px; border-radius:9px; transition:background .15s; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:#FFF0F2; color:#CB1330; font-weight:650; }
[data-testid="stWidgetLabel"] p { color:#525B6B; font-weight:600; font-size:13px; }
[data-testid="stMultiSelect"] [data-baseweb="tag"] { background:#FFF0F2; color:#B9142E; }
.vtp-hero { position:relative; overflow:hidden; background:#fff; border:1px solid #E7EAF0; border-radius:16px; padding:25px 28px; box-shadow:0 3px 18px #20263204; margin-bottom:5px; }
.vtp-hero:before { content:""; position:absolute; left:0; top:0; bottom:0; width:5px; background:#E51937; }
.vtp-eyebrow { color:#B71630; font-size:10px; font-weight:800; letter-spacing:1.8px; margin-bottom:8px; }
.vtp-hero h1 { margin:0 0 7px !important; padding:0; line-height:1.25; }
.vtp-hero p { color:#7D8593; font-size:13px; margin:0; line-height:1.8; }
.vtp-live { float:right; font-size:11px; font-weight:650; padding:7px 11px; border-radius:7px; color:#596273; background:#F4F5F7; letter-spacing:.2px; }
.vtp-context { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0 3px; }
.vtp-context span { font-size:12px; color:#697284; padding-right:14px; border-right:1px solid #E4E7ED; }
.vtp-context strong { font-size:15px; color:#262D3B; margin-right:4px; }
[data-testid="stMetric"] { position:relative; background:white; padding:23px 22px 18px; border-radius:14px; border:1px solid #E7EAF0; border-top:3px solid #E51937; min-height:153px; box-shadow:0 3px 15px #20263203; }
[data-testid="stMetricLabel"] { min-height:42px; align-items:flex-start; }
[data-testid="stMetricLabel"] p { font-size:13px; color:#70798A; white-space:normal; line-height:1.45; font-weight:550; }
[data-testid="stMetricValue"] { font-size:2.35rem; font-weight:750; line-height:1.35; letter-spacing:-1.5px; color:#242B38; }
[data-testid="stMetric"] [data-testid="stTooltipIcon"] { color:#A5ACB7; }
[data-testid="stDataFrame"] { border-radius:10px; overflow:hidden; border:1px solid #ECEEF2; }
[data-testid="stVerticalBlockBorderWrapper"] > div { border-color:#E7EAF0 !important; border-radius:14px; }
.st-key-branch_summary, .st-key-status_summary, .st-key-age_distribution { background:#fff; border-radius:14px; }
[data-testid="stSidebar"] [data-testid="stAlert"] { background:#FFF0F2; color:#B9142E; }
[data-testid="stSidebar"] [data-testid="stAlert"] p { color:#B9142E; }
[data-testid="stButton"] button[kind="primary"], [data-testid="stDownloadButton"] button[kind="primary"] { background:#E51937; border-color:#E51937; border-radius:8px; font-weight:650; }
[data-testid="stButton"] button[kind="primary"]:hover { background:#CC1330; border-color:#CC1330; }
[data-testid="stDownloadButton"] button { border-radius:8px; }
[data-testid="stAlert"] { border-radius:10px; font-size:13px; }
[data-testid="stCaptionContainer"] { color:#7B8493; font-size:12px; }
[data-testid="stExpander"] { border:1px solid #E7EAF0; border-radius:12px; background:white; }
hr { border-color:#E9ECF1; margin:1rem 0; }
@media (max-width:900px) {
 [data-testid="stMainBlockContainer"] { padding:1.4rem 1rem 2rem; }
 .vtp-hero { padding:21px 19px; }
 .vtp-hero h1 { font-size:1.6rem !important; }
 .vtp-live { float:none; display:inline-block; margin-bottom:12px; }
 [data-testid="stMetricValue"] { font-size:1.95rem; }
}
</style>
"""


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def render_brand():
    st.markdown('<div class="vtp-brand"><div class="vtp-wordmark">VIETTEL<span>POST</span></div>'
                '<small>QUẢN LÝ CÔNG CỤ DỤNG CỤ</small></div>', unsafe_allow_html=True)


def age_chart(ages):
    return _bar_spec(ages, "Nhóm thời gian", "Số bản ghi", AGE_GROUPS,
                     label_limit=145, bar_height=22, height=270, title="Bản ghi")


def status_chart(status):
    return _bar_spec(status, "Tình trạng", "Số mã",
                     ["Đang sử dụng", "Tốt", "Hỏng", "Tiêu hao đã xử lý", "Xuất cho đơn vị"],
                     label_limit=120, bar_height=28, height=290, title="Mã VTTB duy nhất")


def _bar_spec(series, category, measure, order, *, label_limit, bar_height, height, title):
    # Streamlit renders Vega-Lite directly. Avoid importing Altair's Python
    # schema wrappers: Altair 5.5 raises TypedDict(closed=...) on Python 3.14.
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{category: str(label), measure: int(value)} for label, value in series.items()]},
        "mark": {"type": "bar", "color": RED, "cornerRadiusEnd": 4, "height": bar_height},
        "encoding": {
            "y": {"field": category, "type": "nominal", "sort": list(order),
                  "axis": {"title": None, "labelLimit": label_limit}},
            "x": {"field": measure, "type": "quantitative", "axis": {"title": title, "tickMinStep": 1}},
            "tooltip": [{"field": category, "type": "nominal"},
                        {"field": measure, "type": "quantitative", "format": ","}],
        },
        "height": height,
        "config": {"view": {"stroke": None}, "axis": {
            "gridColor": "#EEF0F4", "labelColor": "#737B8C", "titleColor": "#737B8C"}},
    }


def render_header(page, cutoff, today, filtered, total, branches, units, source):
    st.markdown(f'''<section class="vtp-hero">
<span class="vtp-live">{escape(source)}</span>
<div class="vtp-eyebrow">VIETTEL POST / QUẢN TRỊ TÀI SẢN</div>
<h1>{escape(page)}</h1>
<p>Dữ liệu chốt <strong>{format_date(cutoff)}</strong> &nbsp;·&nbsp;
Tuổi sử dụng tính đến {format_date(today)} (Việt Nam)</p>
<div class="vtp-context"><span><strong>{filtered:,}</strong> bản ghi sau lọc</span>
<span><strong>{branches:,}</strong> chi nhánh</span><span><strong>{units:,}</strong> đơn vị quản lý</span>
<span><strong>{total:,}</strong> bản ghi nguồn</span></div></section>''', unsafe_allow_html=True)
