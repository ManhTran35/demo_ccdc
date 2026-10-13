"""Run: python -m streamlit run app.py"""
from datetime import date
import hashlib
import json

import pandas as pd
import streamlit as st

from core import (
    AGE_GROUPS, DETAIL_COLUMNS, ROOT, UNKNOWN, UNCLASSIFIED, VersionStore,
    apply_filters, classification_map, export_csv, export_excel, export_frame,
    metrics, normalize, overview_tables, read_table, review_names, seed_store,
    suggestions, vn_today,
)

st.set_page_config(page_title="CCDC · Theo dõi tồn", page_icon="📦", layout="wide")
st.markdown("""<style>
.stApp{background:#f5f7fb}h1,h2,h3{color:#152744}
[data-testid=stMetric]{background:white;padding:20px;border-radius:14px;border:1px solid #e4e9f2}
[data-testid=stSidebar]{background:#fff}div.stButton>button[kind=primary]{background:#146e72}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_store():
    store = VersionStore()
    seed_store(store)
    return store


@st.cache_data(show_spinner="Đang chuẩn hóa bản chụp dữ liệu…", max_entries=5)
def load_version(version_id, today_iso):
    payload = VersionStore().load(version_id)
    df, report = normalize(payload["table"], payload["mapping"], date.fromisoformat(today_iso))
    return df, report, payload["mapping"]


@st.fragment(run_every="30s")
def refresh_day(rendered_day):
    if vn_today().isoformat() != rendered_day:
        st.rerun(scope="app")


def quality_report(report):
    columns = st.columns(4)
    for c, label, key in zip(columns, ["Thiếu ngày", "Ngày không hợp lệ / tương lai", "Bản ghi có serial nghi trùng", "Tên cần xử lý"], ["missing_date", "invalid_date", "duplicate_serial_rows", "review_names"]):
        value = report[key] + report["future_date"] if key == "invalid_date" else report[key]
        c.metric(label, f"{value:,}".replace(",", "."))
    st.caption(f"Trước ghép: {report['source_rows']:,} dòng · Sau ghép: {report['joined_rows']:,} dòng. Giữ nguyên mọi bản ghi. {report['review_rows']:,} dòng bị ảnh hưởng bởi tên chưa phân loại/cần rà soát; {report['duplicate_rows']:,} dòng nghi giống nhau; {report['missing_code']:,} dòng thiếu mã.")
    if report["errors"]:
        for error in report["errors"]:
            st.error(error)
    else:
        st.success("Cấu trúc và số lượng hợp lệ. Cảnh báo chất lượng vẫn được giữ để rà soát; không tự xóa bản ghi.")


def administration(store, role, today):
    st.title("Cập nhật dữ liệu & phân loại")
    st.info("Vai trò chỉ là mô phỏng trong bản local. Người dùng có thể tự đổi vai trò; đây không phải đăng nhập hoặc phân quyền bảo mật.")
    upload_tab, history_tab, mapping_tab = st.tabs(["Nhập & duyệt phiên", "Lịch sử / khôi phục", "Bảng phân loại"])
    with upload_tab:
        st.write("Mỗi file là một bản chụp toàn bộ. Công bố phiên mới không cộng dồn số dòng với phiên cũ.")
        cutoff = st.date_input("Ngày chốt của file mới", value=today, key="upload_cutoff")
        uploaded = st.file_uploader("Chọn file hiện trạng CCDC (.xlsx)", type=["xlsx"])
        st.caption("Tối đa 25 MB. Ngày thiếu/không hợp lệ và serial nghi trùng là cảnh báo; số lượng thiếu, âm hoặc không phải số chặn công bố.")
        if st.button("Kiểm tra & tạo bản xem trước", disabled=uploaded is None or role == "Người xem", type="primary"):
            content = uploaded.getvalue()
            if len(content) > 25 * 1024 * 1024:
                st.error("File vượt giới hạn 25 MB.")
            else:
                digest = hashlib.sha256(content).hexdigest()
                (store.root / "originals" / (digest + ".xlsx")).write_bytes(content)
                try:
                    current = store.latest()
                    mapping_file = store.root / "classification.json"
                    mapping = json.loads(mapping_file.read_text(encoding="utf-8")) if mapping_file.exists() else (store.load(current["id"])["mapping"] if current else {})
                    with st.spinner("Đang đọc Excel và kiểm tra…"):
                        table = read_table(content)
                        _, report = normalize(table, mapping, today)
                        version_id = store.create(content, uploaded.name, cutoff.isoformat(), table, mapping)
                    st.session_state["preview_id"] = version_id
                    st.success("Đã lưu nguyên file gốc và tạo phiên chờ duyệt. Phiên hiện hành chưa thay đổi.")
                except Exception as exc:
                    st.error(f"Không thể tạo bản xem trước: {exc}. File gốc đã lưu; phiên hiện hành không thay đổi.")
        pending = [v for v in store.list() if not v["published_at"]]
        if pending:
            options = {v["id"]: f"{v['name']} · chốt {v['cutoff']} · {v['id'][:8]}" for v in pending}
            default = st.session_state.get("preview_id")
            idx = list(options).index(default) if default in options else 0
            pending_id = st.selectbox("Phiên chờ duyệt", list(options), format_func=options.get, index=idx)
            try:
                df, report, mapping = load_version(pending_id, today.isoformat())
                quality_report(report)
                st.dataframe(df[DETAIL_COLUMNS].head(100), hide_index=True)
                names = review_names(df, mapping)
                st.subheader("Tên chưa phân loại / cần rà soát")
                st.dataframe(names, hide_index=True)
                if not names.empty:
                    chosen = st.selectbox("Tên cần xem gợi ý", names["Tên VTTB"].tolist())
                    st.write("Tên gần giống để tham khảo, không tự gán:", suggestions(chosen, mapping) or "Không có gợi ý phù hợp.")
                if st.button("Công bố phiên này", type="primary", disabled=role != "Người duyệt" or bool(report["errors"])):
                    store.publish(pending_id, role)
                    st.session_state.pop("as_of", None)
                    st.success("Đã công bố. Mở Tổng quan để xem phiên mới.")
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        else:
            st.caption("Chưa có phiên chờ duyệt.")
    with history_tab:
        versions = store.list()
        st.dataframe(pd.DataFrame([{**v, "Trạng thái": "Đã công bố" if v["published_at"] else "Chờ duyệt"} for v in versions]), hide_index=True)
        published = store.list(True)
        if published:
            labels = {v["id"]: f"{v['cutoff']} · {v['name']} · {v['id'][:8]}" for v in published}
            selected = st.selectbox("Chọn phiên đã công bố để xem lại", list(labels), format_func=labels.get)
            old, report, mapping = load_version(selected, today.isoformat())
            st.caption(f"{len(old):,} dòng; tuổi sử dụng được tính lại đến {today.strftime('%d/%m/%Y')}.")
            st.dataframe(old[DETAIL_COLUMNS].head(100), hide_index=True)
            meta = next(v for v in published if v["id"] == selected)
            st.download_button("Tải nguyên file gốc", (store.root / "originals" / (meta["digest"] + ".xlsx")).read_bytes(), file_name=meta["name"])
            if st.button("Khôi phục bản chụp này", disabled=role != "Người duyệt"):
                store.restore(selected, role)
                st.session_state.pop("as_of", None)
                st.success("Đã tạo lần công bố mới từ bản chụp cũ, giữ nguyên ngày chốt; không xóa lịch sử.")
                st.rerun()
    with mapping_tab:
        st.write("Ghép chính xác sau chuẩn hóa khoảng trắng và chữ hoa/thường. Tên tham chiếu xung đột được đánh dấu cần rà soát; số dòng dữ liệu không tăng.")
        file = st.file_uploader("Thay bảng phân loại (.xlsx, sheet Chi_tiet)", type=["xlsx"], key="classification_upload")
        if st.button("Kiểm tra bảng phân loại", disabled=file is None):
            try:
                mapping, report = classification_map(read_table(file.getvalue(), True))
                st.session_state["mapping_preview"] = (mapping, report, file.getvalue())
            except Exception as exc:
                st.error(str(exc))
        if "mapping_preview" in st.session_state:
            mapping, report, original = st.session_state["mapping_preview"]
            st.json(report)
            st.dataframe(pd.DataFrame([[k] + v for k, v in mapping.items()], columns=["Tên chuẩn hóa", "Loại VTTB", "Nhóm lớn", "Trạng thái phân loại", "Ghi chú"]), hide_index=True)
            if st.button("Lưu bảng phân loại cho lần nhập tiếp theo", disabled=role != "Người duyệt"):
                (store.root / "classification.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
                (store.root / "originals" / (hashlib.sha256(original).hexdigest() + ".xlsx")).write_bytes(original)
                st.success("Đã lưu. Các phiên đã công bố giữ nguyên bảng phân loại của phiên đó; nhập lại file hiện trạng và duyệt để áp dụng phân loại mới.")
        st.caption("Bảng phân loại mẫu được giữ tại data/samples/classification.xlsx.")


def main():
    store = get_store()
    today = vn_today()
    refresh_day(today.isoformat())
    with st.sidebar:
        st.markdown("## 📦 CCDC\nTheo dõi tồn & vòng đời")
        page = st.radio("Điều hướng", ["Tổng quan", "Chi tiết dữ liệu", "Quản trị"])
        role = st.selectbox("Vai trò mô phỏng", ["Người xem", "Người nhập", "Người duyệt"])
        st.caption("Mô phỏng, không phải đăng nhập bảo mật.")
    if page == "Quản trị":
        administration(store, role, today)
        return
    latest = store.latest()
    if not latest:
        st.warning("Chưa có phiên đã công bố. Vào Quản trị để nhập và duyệt dữ liệu.")
        return
    with st.sidebar:
        st.divider()
        st.markdown("### Bộ lọc dùng chung")
        cutoff = st.date_input("Ngày chốt dữ liệu", value=date.fromisoformat(latest["cutoff"]), key="as_of")
    version = store.as_of(cutoff.isoformat())
    if not version:
        st.title(page)
        st.info("Chưa có phiên công bố tính đến ngày đã chọn. Không lấy dữ liệu ở thời điểm tương lai.")
        return
    df, report, mapping = load_version(version["id"], today.isoformat())
    with st.sidebar:
        branches = st.multiselect("Chi nhánh", sorted(df["Chi nhánh"].unique()), key="branches")
        possible_managers = sorted(df.loc[df["Chi nhánh"].isin(branches) if branches else pd.Series(True, index=df.index), "Đơn vị quản lý"].unique())
        if "managers" in st.session_state:
            st.session_state["managers"] = [x for x in st.session_state["managers"] if x in possible_managers]
        managers = st.multiselect("Đơn vị quản lý", possible_managers, key="managers")
        types = st.multiselect("Loại VTTB", sorted(set(df["Loại VTTB"]) | {UNCLASSIFIED}), key="types")
        statuses = st.multiselect("Tình trạng", sorted(df["Tình trạng"].unique()), key="statuses")
        search = st.text_input("Tìm mã, tên, serial, đơn vị", key="search")
        issue = st.selectbox("Chỉ báo chất lượng", ["", "Thiếu ngày", "Ngày không hợp lệ", "Ngày ở tương lai", "Serial nghi trùng", UNCLASSIFIED, "Phân loại cần rà soát", "Dòng nghi trùng"], format_func=lambda x: x or "Tất cả", key="issue")
        st.caption("Không chọn giá trị = tất cả. Tìm kiếm và chỉ báo chất lượng cũng áp dụng cho KPI và xuất file.")
    filtered = apply_filters(df, branches, managers, types, statuses, search, issue)
    st.title(page)
    st.caption(f"Dữ liệu chốt ngày {date.fromisoformat(version['cutoff']).strftime('%d/%m/%Y')} · Tuổi sử dụng tính đến ngày {today.strftime('%d/%m/%Y')} (Việt Nam) · Phiên {version['id'][:8]}")
    st.write(f"**{len(filtered):,} / {len(df):,} bản ghi** khớp bộ lọc hiện tại.")
    m = metrics(filtered)
    if page == "Tổng quan":
        a, b, c = st.columns(3)
        a.metric("Số mã CCDC đang sử dụng", f"{m['using_codes']:,}".replace(",", "."), help="Số Mã VTTB duy nhất (không rỗng) trên các bản ghi Đang sử dụng sau lọc.")
        b.metric("Số mã vật tư chưa có ngày đưa vào sử dụng", f"{m['material_missing_codes']:,}".replace(",", "."), help="Mã VTTB duy nhất, Chủng loại = Vật tư, ngày nguồn để trống. Không kết luận tất cả vật tư này chưa sử dụng.")
        c.metric("Tỷ lệ bản ghi thiết bị hỏng", "—" if m["broken_rate"] is None else f"{m['broken_rate']:.3f}%".replace(".", ","), help=f"Tử số: {m['broken_rows']} bản ghi Thiết bị có tình trạng Hỏng. Mẫu số: {m['device_rows']} bản ghi Thiết bị sau lọc. Không cộng Số lượng hay ĐVT.")
        branch, status, ages, excluded = overview_tables(filtered)
        left, right = st.columns([2, 1])
        with left:
            st.subheader("Số mã VTTB theo chi nhánh và tình trạng")
            st.dataframe(branch, height=360)
        with right:
            st.subheader("Số mã VTTB theo tình trạng")
            st.dataframe(status, height=360)
        st.caption("Đếm mã duy nhất trong từng nhóm. Một mã có thể thuộc nhiều chi nhánh hoặc trạng thái; tổng các nhóm có thể lớn hơn số mã duy nhất toàn bộ.")
        st.subheader("Phân bố thời gian sử dụng · bản ghi")
        if int(ages.sum()):
            st.bar_chart(ages, color="#168b82")
        else:
            st.info("Không có bản ghi Đang sử dụng với ngày hợp lệ trong bộ lọc hiện tại. Kiểm tra bộ lọc tình trạng/ngày.")
        st.caption(f"Chỉ lấy bản ghi Đang sử dụng có ngày hợp lệ. {excluded:,} bản ghi Đang sử dụng bị loại vì ngày chưa xác định. Tuổi là số tháng tròn, không phải số lượng thiết bị.")
        with st.expander("Chất lượng của tập dữ liệu đang lọc"):
            for flag in ["Thiếu ngày", "Ngày không hợp lệ", "Ngày ở tương lai", "Serial nghi trùng", UNCLASSIFIED, "Phân loại cần rà soát"]:
                st.write(f"{flag}: {int(filtered['Vấn đề dữ liệu'].str.contains(flag, regex=False).sum()):,} bản ghi")
            st.dataframe(review_names(filtered, mapping), hide_index=True)
    else:
        extras = st.multiselect("Thêm cột dữ liệu / giá trị gốc", [c for c in df.columns if c not in DETAIL_COLUMNS])
        columns = DETAIL_COLUMNS + extras
        page_size = st.selectbox("Số dòng mỗi trang", [50, 100, 200, 500], index=1)
        pages = max(1, math_ceil(len(filtered), page_size))
        page_no = st.number_input("Trang", min_value=1, max_value=pages, value=1, step=1, key=f"detail_page_{len(filtered)}_{page_size}")
        st.caption(f"Trang {page_no}/{pages}. File tải xuống chứa toàn bộ bản ghi sau lọc, không chỉ trang này.")
        display = filtered[columns].iloc[(page_no - 1) * page_size:page_no * page_size].copy()
        if "Số tháng sử dụng" in display:
            display["Số tháng sử dụng"] = display["Số tháng sử dụng"].astype("string").fillna(UNKNOWN)
        st.dataframe(display, hide_index=True, height=500)
        signature = hashlib.sha256(repr((version["id"], today, branches, managers, types, statuses, search, issue, columns)).encode()).hexdigest()
        if st.button("Chuẩn bị CSV & Excel của dữ liệu sau lọc", type="primary"):
            with st.spinner("Đang tạo file xuất…"):
                out = export_frame(filtered, columns, version["cutoff"], today)
                st.session_state["exports"] = (signature, export_csv(out), export_excel(out))
        if "exports" in st.session_state and st.session_state["exports"][0] == signature:
            _, csv, excel = st.session_state["exports"]
            st.download_button("Tải CSV", csv, f"CCDC_{version['cutoff']}_tuoi_{today.isoformat()}.csv", "text/csv")
            st.download_button("Tải Excel", excel, f"CCDC_{version['cutoff']}_tuoi_{today.isoformat()}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("Mã và serial giữ dưới dạng chuỗi; CSV không có kiểu cột, nên khi nhập CSV vào Excel hãy chọn kiểu Text cho cột mã/serial. Excel (.xlsx) giữ kiểu chuỗi trực tiếp.")


def math_ceil(a, b):
    return (a + b - 1) // b


if __name__ == "__main__":
    main()
