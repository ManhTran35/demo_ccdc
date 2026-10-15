# Rà soát kiến trúc hiện tại — 15/10/2026

Baseline: commit `c59172837920eba11694ba2e56a180017212bdec`. Báo cáo lập trước khi sửa ứng dụng theo đặc tả online 1.0.

## Framework và entry point xác minh từ source

`app.py` import Streamlit, khai báo `st.set_page_config`, gọi `main()`. Lệnh thật: `python -m streamlit run app.py`. Đây là ứng dụng Streamlit thuần, có WebSocket. `package.json` chỉ cung cấp test Playwright; không có frontend build bắt buộc, Flask, FastAPI hoặc server Node.

`assets/dashboard.template.html`, CSS, JS và SheetJS là bản HTML offline riêng. `scripts/build_html.py` nhúng các asset và dữ liệu vào một HTML; Streamlit không phục vụ file này. Bản HTML có logic JS tương ứng và IndexedDB riêng, không phải frontend của Cloud Run. Giữ asset để bảo toàn demo; image online không cần chúng.

## Luồng hiện tại

Excel nguồn → `core.read_table` (openpyxl, read-only, data-only) → bảng nguồn giữ đủ cột và số dòng Excel → `core.normalize` → DataFrame → `core.metrics`, `overview_tables`, `apply_filters`, `export_frame/CSV/Excel` → giao diện Streamlit.

`core.VersionStore`: SQLite `data/runtime/versions.sqlite3`, nguyên file theo SHA tại `originals/`, snapshot bảng nguồn + mapping gzip JSON tại `snapshots/`. `get_store()` tự seed Excel thật trong `data/samples`. Các phiên đã công bố được chọn theo ngày chốt; khôi phục tạo một lần công bố mới. Không có active pointer, GCS, Parquet hoặc khóa publish phân tán. Upload/phân loại/khôi phục trong `app.py` đang truy cập filesystem trực tiếp.

`st.cache_data` khóa theo version ID và ngày tuổi; mỗi version/ngày lại normalize bảng nguồn. Không parse Excel mỗi request nhưng mỗi ngày vẫn chuẩn hóa toàn bộ raw JSON. Tìm kiếm ghép chuỗi các cột trên toàn bộ DataFrame mỗi lần lọc. Chi tiết phân trang 50–500 dòng; export toàn bộ kết quả sau lọc trong bộ nhớ. Fragment kiểm tra qua ngày mỗi 30 giây. Không có đường dẫn Windows trong nghiệp vụ; script môi trường cũ hard-code `/workspace/demo_ccdc` (chỉ cho Codex).

## Nghiệp vụ phải giữ

- `Mã VTTB` là mã loại/mã vật tư, không được coi là ID thiết bị vật lý. Một mã có thể lặp ở nhiều chi nhánh/tình trạng. Serial chỉ là chỉ báo nghi trùng, chưa có khóa giao dịch/thiết bị vật lý được xác nhận; không deduplicate.
- Bộ lọc chi nhánh (`Chi nhánh mới`), đơn vị phụ thuộc, loại có Chưa phân loại, tình trạng, ngày chốt; tìm kiếm và chỉ báo chất lượng cùng áp dụng cho KPI/bảng/export.
- Mã đang sử dụng = mã duy nhất không rỗng trên bản ghi Đang sử dụng. Mã vật tư thiếu ngày = mã duy nhất Chủng loại Vật tư với ô ngày nguồn trống; không đổi nhãn thành “chưa sử dụng”.
- Tỷ lệ bản ghi thiết bị hỏng = bản ghi Thiết bị + Hỏng / toàn bộ bản ghi Thiết bị sau lọc; không cộng khác ĐVT.
- Tuổi tháng tròn tới hôm nay Asia/Ho_Chi_Minh, độc lập ngày chốt. Ngày thiếu/lỗi/tương lai có tuổi Chưa xác định. Biểu đồ chỉ đếm bản ghi Đang sử dụng có ngày hợp lệ.
- Tên ghép đúng sau chuẩn hóa case/whitespace, xung đột không nhân dòng; giữ raw. Mã/serial/export phải là chuỗi. Hai cột Đơn vị quản lý có tên phân biệt; ưu tiên cột thứ hai.

Không có mâu thuẫn KPI cần thay đổi; giữ nguyên định nghĩa đã được người dùng xác nhận.

## Hạng mục sửa theo file

| File/nhóm | Thay đổi dự kiến |
|---|---|
| `core.py` | Giữ API nghiệp vụ và test; bổ sung hàm cập nhật riêng tuổi theo ngày từ Parquet, tối ưu search. Legacy VersionStore chỉ hỗ trợ tương thích offline, không dùng PROD. |
| `app.py` | Thay đọc trực tiếp filesystem bằng repository; cache theo active version và ngày; PROD xác minh IAP JWT, bỏ role picker và tắt web-write V1-A; giữ layout, KPI, bộ lọc, biểu đồ và export. |
| `services/storage.py`, `versions.py` | Byte-object local/GCS, snapshot bất biến, readback checksum/schema, active CAS generation, list/history/rollback. |
| `services/validation.py`, `transform.py` | Validate XLSX trước parse, chuẩn hóa một lần lúc ingestion; Parquet + mapping/raw đối chiếu + manifest/report. |
| `services/config.py`, `auth.py` | Cấu hình env fail closed, ADC không key trong source, IAP audience/issuer/signature và Admin allowlist. |
| `scripts/ingest_excel.py` | Stage → preview KPI/delta → token xác nhận đúng source/state → publish; rollback/list CLI có IAM + Admin allowlist. |
| `Dockerfile`, ignore, deploy scripts | Non-root, PORT, allowlist build context, không image chứa Excel/HTML nhúng dữ liệu; deploy không public, IAP bắt buộc. |
| `data/`, `downloads/`, test demo | Giữ bản sao private local của artifact cũ; bỏ dữ liệu thật khỏi tree mới, tạo mẫu giả bằng script; test không phụ thuộc số liệu thật. |
| `docs/`, README/tests | Hướng dẫn Project/Billing/IAM/IAP và lifecycle, code/data rollback riêng, test mock GCS/CAS/error + UI/concurrency/resource profile. |

## Rủi ro và giới hạn trước triển khai

- Commit baseline đã đưa hai Excel và artifact nhúng dữ liệu lên GitHub theo yêu cầu upload trước đó. Loại khỏi tree mới không xóa lịch sử; cần người sở hữu repository quyết định private/access và xử lý lịch sử. Không tự rewrite hoặc force-push.
- Snapshot/image không được giữ dữ liệu thật. Không được tự seed hoặc fallback về file local trong PROD. Bucket private, runtime read-only ở V1-A; publisher dùng danh tính ADC/IAM riêng.
- Cloud Run filesystem không bền và có thể nhiều instance khi rollout; SQLite và role picker không đáp ứng production. Active pointer phải CAS, không dựa max-instances=1 làm khóa.
- `core.read_table` hiện chưa kiểm tra ZIP bomb, external link/macros/formula. Bổ sung validation trước nhận file online; không tự sửa workbook để qua validation.
- Dữ liệu nguồn và export lớn đang giữ trong RAM. Cần benchmark dữ liệu giả ~100k, Parquet và 5 session trước kết luận 2 GiB đủ.
- Đã phát hiện Docker daemon 28.4.0; chưa thấy `gcloud` trong PATH. Có binding `GOOGLE_APPLICATION_CREDENTIALS` và file readable, chưa kết luận quyền GCP. Không đọc/in giá trị credential. Chưa có Project/Billing/5 user/approval từ người dùng.
- Tài liệu chính thức `docs.cloud.google.com` đang bị egress proxy trả 403; kiểm chứng cờ gcloud/IAP phải dùng nguồn Google khả dụng hoặc hoàn thành sau mở domain, không bỏ bảo mật để thử deploy.
- Chưa benchmark phiên bản nâng cấp tại thời điểm audit; kết quả thực chạy sẽ bổ sung trong `docs/VALIDATION_RESULTS.md`. Không coi snapshot local hay mock là bằng chứng đã go-live.
