"""Run: python -m streamlit run app.py"""
from datetime import date
import hashlib
import json
import logging
import uuid

import pandas as pd
import streamlit as st

from core import (
    AGE_GROUPS, DETAIL_COLUMNS, UNKNOWN, UNCLASSIFIED,
    apply_filters, classification_map, export_csv, export_excel, export_frame,
    metrics, overview_tables, review_names,
    suggestions, vn_today, display_frame, format_date,
)

from services.config import Settings, ConfigurationError
from services.auth import verify_iap_identity, AuthenticationError
from services.repository import DataRepository, live_quality_report
from services.storage import ObjectNotFound
from services.transform import parse_classification, transform_inventory, compare_metrics
from services.validation import ValidationError
from services.presentation import apply_theme, render_brand, render_header, age_chart, status_chart
from services.drive import DriveConfig, DriveClient, DriveRepository, DriveError
from ui_labels import APP_TITLE, PAGE_OVERVIEW, PAGE_DETAILS, PAGE_ADMIN, KPI_USING, KPI_MATERIAL_MISSING, KPI_BROKEN_RATE

logger = logging.getLogger(__name__)

st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")
apply_theme()


@st.cache_resource
def get_store():
    settings = Settings.from_env()
    if settings.drive_mode:
        from streamlit.errors import StreamlitSecretNotFoundError
        try:
            config = DriveConfig.from_mapping(st.secrets.get("google_drive", {}))
            account = st.secrets.get("gcp_service_account")
        except (FileNotFoundError, StreamlitSecretNotFoundError):
            config, account = DriveConfig(), None
        client = DriveClient.from_service_account(account, settings.max_upload_mb * 1024 * 1024)
        return DriveRepository(settings, config, client)
    store = DataRepository(settings)
    store.seed_synthetic_demo()
    return store


@st.cache_resource(show_spinner="Đang đọc bản chụp Parquet…", max_entries=2)
def load_version(version_id, today_iso, namespace, _store):
    today = date.fromisoformat(today_iso)
    df = _store.read_dataset(version_id, today)
    report = live_quality_report(df, _store.manager.read_validation(version_id), today)
    report["dataset_note"] = _store.manager.get_manifest(version_id).get("note", "")
    return df, report, _store.manager.read_mapping(version_id)


def storage_namespace(store):
    c = store.settings
    return f"gcs:{c.project_id}:{c.bucket}:{c.prefix}" if c.cloud_mode else "local:" + str(c.local_data_dir.resolve())


@st.fragment(run_every="30s")
def refresh_state(rendered_day, rendered_version, rendered_generation, _store):
    # Scheduled fragments execute separately from main()'s error boundary.
    try:
        if _store.settings.drive_mode:
            _store.synchronize()
            if _store.last_error:
                st.warning("Đang giữ bản dữ liệu hợp lệ gần nhất. " + _store.last_error)
        active = _store.get_active_version()
    except Exception as exc:
        logger.error("dashboard_refresh_failed error_type=%s", type(exc).__name__)
        return st.rerun(scope="app")
    key = (active["active_version"], active["_generation"]) if active else (None, 0)
    if vn_today().isoformat() != rendered_day or key != (rendered_version, rendered_generation):
        st.rerun(scope="app")


def reset_active_selection():
    st.session_state.pop("as_of", None)
    st.session_state.pop("observed_pointer", None)


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
    if store.settings.read_only:
        raise PermissionError("Phiên bản online này chỉ cho phép xem dữ liệu.")
    manager = store.manager
    actor = "local-admin (mô phỏng)"
    namespace = storage_namespace(store)
    st.title("Cập nhật dữ liệu & phân loại")
    st.info("Vai trò chỉ là mô phỏng trong bản local. Người dùng có thể tự đổi vai trò; đây không phải đăng nhập hoặc phân quyền bảo mật.")
    upload_tab, history_tab, mapping_tab = st.tabs(["Nhập & duyệt phiên", "Lịch sử / khôi phục", "Bảng phân loại"])
    with upload_tab:
        st.write("Mỗi file là một bản chụp toàn bộ. Công bố phiên mới không cộng dồn số dòng với phiên cũ.")
        cutoff = st.date_input("Ngày chốt của file mới", value=today, key="upload_cutoff", format="DD/MM/YYYY")
        uploaded = st.file_uploader("Chọn file hiện trạng CCDC (.xlsx)", type=["xlsx"])
        st.caption(f"Tối đa {store.settings.max_upload_mb} MB. Số lượng lỗi/cột thiếu chặn công bố. File có công thức hoặc liên kết ngoài cần được xuất thành bản chỉ chứa giá trị.")
        if st.button("Kiểm tra & tạo bản xem trước", disabled=uploaded is None or role == "Người xem", type="primary"):
            content = uploaded.getvalue()
            if len(content) > store.settings.max_upload_mb * 1024 * 1024:
                st.error("File vượt giới hạn dung lượng.")
            else:
                stage = manager.stage_upload(content, uploaded.name, actor)
                try:
                    active = manager.get_active()
                    classification_stage = None
                    try:
                        saved = json.loads(manager.storage.get("metadata/local_classification.json").data)
                        mapping = saved["mapping"]
                        classification_stage = saved.get("classification_stage_id")
                    except ObjectNotFound:
                        mapping = manager.read_mapping(active["active_version"]) if active else {}
                        if active:
                            classification_stage = manager.read_validation(active["active_version"]).get("classification_stage_id")
                    with st.spinner("Đang kiểm tra Excel và chuẩn bị Parquet…"):
                        result = transform_inventory(content, mapping, today, store.settings, uploaded.name)
                        if classification_stage:
                            result.report["classification_stage_id"] = classification_stage
                        manager.record_stage_validation(stage["id"], result.report)
                        preview = manager.prepare_version(stage, result.parquet_bytes, mapping, result.report, result.metrics,
                                                          cutoff.isoformat(), actor, active["_generation"] if active else 0)
                    st.session_state["preview_id"] = preview["id"]
                    st.success("Đã lưu nguyên file gốc và tạo phiên chờ duyệt. Phiên hiện hành chưa thay đổi.")
                    if active:
                        st.dataframe(pd.DataFrame(compare_metrics(result.metrics, manager.read_metrics(active["active_version"]))).T)
                except ValidationError as exc:
                    manager.record_stage_validation(stage["id"], exc.report)
                    st.error(str(exc) + " File gốc đã được lưu ở staging; phiên hiện hành không thay đổi.")
        pending = [v for v in store.list_versions() if not v["published"]]
        if pending:
            options = {v["id"]: f"{v['source_filename']} · chốt {format_date(v['data_date'])} · {v['id']}" for v in pending}
            default = st.session_state.get("preview_id")
            idx = list(options).index(default) if default in options else 0
            pending_id = st.selectbox("Phiên chờ duyệt", list(options), format_func=options.get, index=idx)
            df, report, mapping = load_version(pending_id, today.isoformat(), namespace, store)
            quality_report(report)
            st.dataframe(display_frame(df[DETAIL_COLUMNS].head(100)), hide_index=True)
            names = review_names(df, mapping)
            st.subheader("Tên chưa phân loại / cần rà soát")
            st.dataframe(names, hide_index=True)
            if not names.empty:
                chosen = st.selectbox("Tên cần xem gợi ý", names["Tên VTTB"].tolist())
                st.write("Tên gần giống để tham khảo, không tự gán:", suggestions(chosen, mapping) or "Không có gợi ý phù hợp.")
            confirmed = st.checkbox("Tôi đã kiểm tra bản xem trước và xác nhận thay thế toàn bộ phiên hiện hành", key=f"confirm_{pending_id}")
            if st.button("Công bố phiên này", type="primary", disabled=role != "Người duyệt" or not confirmed or bool(report["errors"])):
                preview = manager.get_preview(pending_id)
                manager.commit(pending_id, preview["confirmation_token"], actor, preview["expected_active_generation"])
                reset_active_selection()
                st.rerun()
        else:
            st.caption("Chưa có phiên chờ duyệt.")
    with history_tab:
        published = [v for v in store.list_versions() if v["published"]]
        st.dataframe(pd.DataFrame([{"Phiên": v["id"], "Ngày chốt": format_date(v["data_date"]), "File": v["source_filename"], "Số dòng": v["row_count"], "Hiện hành": v["current"], "Người công bố": v["published_by"]} for v in published]), hide_index=True)
        if published:
            labels = {v["id"]: f"{format_date(v['data_date'])} · {v['source_filename']} · {v['id']}" for v in published}
            selected = st.selectbox("Chọn phiên đã công bố để xem lại", list(labels), format_func=labels.get)
            old, report, mapping = load_version(selected, today.isoformat(), namespace, store)
            st.caption(f"{len(old):,} dòng; tuổi sử dụng tính lại đến {today.strftime('%d/%m/%Y')}.")
            st.dataframe(display_frame(old[DETAIL_COLUMNS].head(100)), hide_index=True)
            meta = next(v for v in published if v["id"] == selected)
            st.download_button("Tải nguyên file gốc", manager.read_source(selected), file_name=meta["source_filename"])
            active = manager.get_active()
            preview = manager.preview_rollback(selected, active["_generation"])
            confirm = st.checkbox("Xác nhận chuyển phiên hiện hành về bản chụp đã chọn", key=f"confirm_rollback_{selected}_{active['_generation']}")
            if st.button("Khôi phục bản chụp này", disabled=role != "Người duyệt" or not confirm):
                manager.rollback(selected, actor, active["_generation"], preview["confirmation_token"])
                reset_active_selection()
                st.rerun()
    with mapping_tab:
        st.write("Ghép đúng tên sau chuẩn hóa khoảng trắng và chữ hoa/thường. Phân loại xung đột được đánh dấu cần rà soát, không nhân dòng nguồn.")
        file = st.file_uploader("Thay bảng phân loại (.xlsx, sheet Chi_tiet)", type=["xlsx"], key="classification_upload")
        if st.button("Kiểm tra bảng phân loại", disabled=file is None or role == "Người xem"):
            if file.size > store.settings.max_upload_mb * 1024 * 1024:
                st.error("File vượt giới hạn dung lượng.")
            else:
                stage = manager.stage_upload(file.getvalue(), file.name, actor)
                try:
                    mapping, report = parse_classification(file.getvalue(), store.settings, file.name)
                    manager.record_stage_validation(stage["id"], report)
                    st.session_state["mapping_preview"] = (mapping, report, stage["id"])
                except ValidationError as exc:
                    manager.record_stage_validation(stage["id"], exc.report)
                    st.error(str(exc))
        if "mapping_preview" in st.session_state:
            mapping, report, stage_id = st.session_state["mapping_preview"]
            st.json(report)
            st.dataframe(pd.DataFrame([[k] + v for k, v in mapping.items()], columns=["Tên chuẩn hóa", "Loại VTTB", "Nhóm lớn", "Trạng thái phân loại", "Ghi chú"]), hide_index=True)
            if st.button("Lưu bảng phân loại cho lần nhập tiếp theo", disabled=role != "Người duyệt"):
                key = "metadata/local_classification.json"
                try:
                    generation = manager.storage.get(key).generation
                except ObjectNotFound:
                    generation = 0
                manager.storage.put(key, json.dumps({"mapping": mapping, "classification_stage_id": stage_id}, ensure_ascii=False).encode(), if_generation_match=generation, content_type="application/json")
                st.success("Đã lưu cho lần nhập tiếp theo. Các phiên đã công bố giữ nguyên phân loại của phiên đó.")


def main():
    settings = Settings.from_env()
    with st.sidebar:
        render_brand()
    identity = verify_iap_identity(st.context.headers, settings) if settings.cloud_mode else None
    store = get_store()
    if settings.drive_mode:
        with st.spinner("Đang kiểm tra bản chụp Google Drive…"):
            store.synchronize()
    today = vn_today()
    active = store.get_active_version()
    refresh_state(today.isoformat(), active["active_version"] if active else None, active["_generation"] if active else 0, store)
    with st.sidebar:
        page = st.radio("Điều hướng", [PAGE_OVERVIEW, PAGE_DETAILS] if settings.read_only else [PAGE_OVERVIEW, PAGE_DETAILS, PAGE_ADMIN])
        role = "Người xem"
        if identity:
            st.caption("Đã xác minh qua IAP: " + identity.email)
            st.caption("V1-A: cập nhật dữ liệu bằng CLI dành cho Admin được cấp IAM.")
        elif settings.drive_mode:
            st.caption("Nguồn dữ liệu · Google Drive")
            st.caption("Bản chụp toàn bộ · Ton_CCDC_ddmmyyyy")
            st.button("Làm mới dữ liệu", on_click=store.synchronize, kwargs={"force": True}, use_container_width=True)
        elif settings.community_demo:
            st.info("Demo online · dữ liệu giả · chỉ xem và xuất file.")
        else:
            role = st.selectbox("Vai trò mô phỏng", ["Người xem", "Người nhập", "Người duyệt"])
            st.caption("Local: mô phỏng, không phải đăng nhập bảo mật.")
    if page == PAGE_ADMIN:
        administration(store, role, today)
        return
    if not active:
        st.warning("Chưa có phiên đã công bố. Admin cần validate và publish bằng script ingestion.")
        return
    observed = (active["active_version"], active["_generation"])
    if st.session_state.get("observed_pointer") != observed:
        st.session_state.pop("as_of", None)
        st.session_state["observed_pointer"] = observed
    with st.sidebar:
        st.divider()
        st.markdown("### Bộ lọc dữ liệu")
        cutoff = st.date_input("Ngày chốt dữ liệu", value=date.fromisoformat(active["data_date"]), key="as_of", format="DD/MM/YYYY")
    version = store.select_version(cutoff.isoformat(), active)
    if not version:
        st.title(page)
        st.info("Chưa có phiên công bố tính đến ngày đã chọn. Không lấy dữ liệu ở thời điểm tương lai.")
        return
    df, report, mapping = load_version(version["id"], today.isoformat(), storage_namespace(store), store)
    if settings.drive_mode:
        st.sidebar.caption("File đang xem: " + report.get("drive", {}).get("source_name", ""))
    if "DỮ LIỆU GIẢ" in report.get("dataset_note", "") and not settings.community_demo:
        st.sidebar.info("Đang xem dữ liệu giả để trình diễn dashboard.")
    for key, column in [("branches", "Chi nhánh"), ("types", "Loại VTTB"), ("statuses", "Tình trạng")]:
        if key in st.session_state:
            valid = set(df[column].unique())
            st.session_state[key] = [value for value in st.session_state[key] if value in valid]
    with st.sidebar:
        branches = st.multiselect("Chi nhánh", sorted(df["Chi nhánh"].unique()), key="branches", placeholder="Tất cả chi nhánh")
        possible_managers = sorted(df.loc[df["Chi nhánh"].isin(branches) if branches else pd.Series(True, index=df.index), "Đơn vị quản lý"].unique())
        if "managers" in st.session_state:
            st.session_state["managers"] = [x for x in st.session_state["managers"] if x in possible_managers]
        managers = st.multiselect("Đơn vị quản lý", possible_managers, key="managers", placeholder="Tất cả đơn vị")
        types = st.multiselect("Loại VTTB", sorted(set(df["Loại VTTB"]) | {UNCLASSIFIED}), key="types", placeholder="Tất cả loại VTTB")
        statuses = st.multiselect("Tình trạng", sorted(df["Tình trạng"].unique()), key="statuses", placeholder="Tất cả tình trạng")
        search = st.text_input("Tìm mã, tên, serial, đơn vị", key="search", placeholder="Nhập mã, tên hoặc serial…")
        issue = st.selectbox("Chỉ báo chất lượng", ["", "Thiếu ngày", "Ngày không hợp lệ", "Ngày ở tương lai", "Serial nghi trùng", UNCLASSIFIED, "Phân loại cần rà soát", "Dòng nghi trùng"], format_func=lambda x: x or "Tất cả", key="issue")
        st.caption("Không chọn giá trị = tất cả. Tìm kiếm và chỉ báo chất lượng cũng áp dụng cho KPI và xuất file.")
    filtered = apply_filters(df, branches, managers, types, statuses, search, issue)
    source = "Google Drive" if settings.drive_mode else "Google Cloud" if settings.cloud_mode else "Dữ liệu mẫu" if settings.community_demo else "Dữ liệu local"
    render_header(page, version["cutoff"], today, len(filtered), len(df),
                  filtered["Chi nhánh"].nunique(), filtered["Đơn vị quản lý"].nunique(), source)
    st.write(f"**{len(filtered):,} / {len(df):,} bản ghi** khớp bộ lọc hiện tại.")
    m = metrics(filtered)
    if page == "Tổng quan":
        a, b, c = st.columns(3)
        a.metric(KPI_USING, f"{m['using_codes']:,}".replace(",", "."), help="Số Mã VTTB duy nhất (không rỗng) trên các bản ghi Đang sử dụng sau lọc.")
        b.metric(KPI_MATERIAL_MISSING, f"{m['material_missing_codes']:,}".replace(",", "."), help="Mã VTTB duy nhất, Chủng loại = Vật tư, ngày nguồn để trống. Không kết luận tất cả vật tư này chưa sử dụng.")
        c.metric(KPI_BROKEN_RATE, "—" if m["broken_rate"] is None else f"{m['broken_rate']:.3f}%".replace(".", ","), help=f"Tử số: {m['broken_rows']} bản ghi Thiết bị có tình trạng Hỏng. Mẫu số: {m['device_rows']} bản ghi Thiết bị sau lọc. Không cộng Số lượng hay ĐVT.")
        branch, status, ages, excluded = overview_tables(filtered)
        left, right = st.columns([2, 1])
        with left, st.container(border=True, key="branch_summary"):
            st.subheader("Số mã VTTB theo chi nhánh và tình trạng")
            st.dataframe(branch, height=360)
        with right, st.container(border=True, key="status_summary"):
            st.subheader("Số mã VTTB theo tình trạng")
            chart_tab, table_tab = st.tabs(["Biểu đồ", "Bảng số liệu"])
            with chart_tab:
                st.altair_chart(status_chart(status), use_container_width=True)
            with table_tab:
                st.dataframe(status, height=300)
        st.caption("Đếm mã duy nhất trong từng nhóm. Một mã có thể thuộc nhiều chi nhánh hoặc trạng thái; tổng các nhóm có thể lớn hơn số mã duy nhất toàn bộ.")
        with st.container(border=True, key="age_distribution"):
            st.subheader("Phân bố thời gian sử dụng · bản ghi")
            if int(ages.sum()):
                st.altair_chart(age_chart(ages), use_container_width=True)
            else:
                st.info("Không có bản ghi Đang sử dụng với ngày hợp lệ trong bộ lọc hiện tại. Kiểm tra bộ lọc tình trạng/ngày.")
            st.caption(f"Chỉ lấy bản ghi Đang sử dụng có ngày hợp lệ. {excluded:,} bản ghi Đang sử dụng bị loại vì ngày chưa xác định. Tuổi là số tháng tròn, không phải số lượng thiết bị.")
        with st.expander("Chất lượng của tập dữ liệu đang lọc"):
            for flag in ["Thiếu ngày", "Ngày không hợp lệ", "Ngày ở tương lai", "Serial nghi trùng", UNCLASSIFIED, "Phân loại cần rà soát"]:
                st.write(f"{flag}: {int(filtered['Vấn đề dữ liệu'].str.contains(flag, regex=False).sum()):,} bản ghi")
            st.dataframe(review_names(filtered, mapping), hide_index=True)
    else:
        extras = st.multiselect("Thêm cột dữ liệu / giá trị gốc", [c for c in df.columns if c not in DETAIL_COLUMNS and not c.startswith("_")])
        columns = DETAIL_COLUMNS + extras
        page_size = st.selectbox("Số dòng mỗi trang", [50, 100, 200, 500], index=1)
        pages = max(1, math_ceil(len(filtered), page_size))
        page_no = st.number_input("Trang", min_value=1, max_value=pages, value=1, step=1, key=f"detail_page_{len(filtered)}_{page_size}")
        st.caption(f"Trang {page_no}/{pages}. File tải xuống chứa toàn bộ bản ghi sau lọc, không chỉ trang này.")
        display = display_frame(filtered[columns].iloc[(page_no - 1) * page_size:page_no * page_size])
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
    try:
        main()
    except AuthenticationError as exc:
        st.error(str(exc))
        st.stop()
    except ConfigurationError:
        st.error("Cấu hình môi trường chưa hợp lệ. Quản trị viên cần kiểm tra các biến môi trường theo tài liệu triển khai.")
        st.stop()
    except DriveError as exc:
        st.error(str(exc))
        st.info("Cấu hình nguồn tại Settings → Secrets của Streamlit. Hướng dẫn: docs/GOOGLE_DRIVE.md.")
        st.stop()
    except Exception as exc:
        error_id = uuid.uuid4().hex[:12]
        logger.error("dashboard_operation_failed error_id=%s error_type=%s", error_id, type(exc).__name__)
        st.error(f"Không thể hoàn tất thao tác dữ liệu. Phiên trước không bị thay đổi nếu chưa công bố thành công. Hãy tải lại và liên hệ quản trị với mã {error_id}.")
        st.stop()
