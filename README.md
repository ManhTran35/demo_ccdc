# Dashboard tồn CCDC — Streamlit Community Cloud & Local

Host đích là **Streamlit Community Cloud**. Entry point online `streamlit_app.py` dùng giao diện đỏ–trắng theo Viettel Post, cùng KPI và bộ lọc trong `app.py`. Hỗ trợ **Google Drive**: thư mục `Data`, file `Ton_CCDC_ddmmyyyy.xlsx`, chọn bản chụp theo ngày trong tên và dùng file tham chiếu đã cung cấp. Xem [thiết lập Google Drive](docs/GOOGLE_DRIVE.md) và [triển khai Streamlit](docs/STREAMLIT_DEPLOY.md).

Ngày nguồn được đọc **dd/mm/yyyy**, không đoán mm/dd/yyyy; ô chọn ngày, bảng và file xuất dùng dd/mm/yyyy. Ngày lưu nội bộ dùng ISO. `services/drive_loader.py` tự đọc **`st.secrets["drive"]`** gồm `folder_id` và `service_account_json`, cache 30 phút và hỗ trợ làm mới ngay. Nếu chưa có cấu hình Drive, app chạy demo có nhãn; khi Drive thiếu quyền/file hợp lệ, app báo lỗi, không dùng dữ liệu giả thay thế. Tên file, ngày dữ liệu, thời gian sửa nguồn và đồng bộ hiển thị ở sidebar.

Mã GCS, snapshot Parquet và IAP từ phương án Cloud Run vẫn được giữ, nhưng không phải điều kiện để triển khai demo lên Streamlit Community Cloud. **Chưa có URL ứng dụng Streamlit được xác minh đang chạy.**

## Quick start local — Windows

Cài Python **3.12**, mở CMD/Terminal tại thư mục repository:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m streamlit run app.py --browser.gatherUsageStats false
```

Không cần activate virtualenv. Lần đầu tự tạo **1.000 dòng dữ liệu giả** tại `data/runtime-v2`, không sử dụng Excel thật. Dừng bằng Ctrl+C. Vai trò ở local chỉ là mô phỏng, không phải đăng nhập. Có thể đổi vị trí lưu bằng biến `LOCAL_DATA_DIR`; không xóa thư mục này khi cập nhật code.

Linux/macOS:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false
```

Ứng dụng không tự load `.env`; `.env.example` là mẫu biến, không chứa credential. Export biến qua shell hoặc cấu hình Cloud Run. Không copy file service-account JSON vào repository.

## Deploy online

Chọn repository `ManhTran35/demo_ccdc`, nhánh `deploy/streamlit-community`, main file `streamlit_app.py`, Python **3.12** tại [Streamlit Community Cloud](https://share.streamlit.io/deploy?repository=ManhTran35%2Fdemo_ccdc&branch=deploy%2Fstreamlit-community&mainModule=streamlit_app.py). Không cần secrets cho demo dữ liệu giả. Để đọc Drive thật, cấu hình [mẫu Secrets](.streamlit/secrets.example.toml), chia sẻ cả thư mục Data và tham chiếu cho service account quyền Viewer theo [hướng dẫn](docs/GOOGLE_DRIVE.md). Chi tiết và giới hạn lưu trữ: [docs/STREAMLIT_DEPLOY.md](docs/STREAMLIT_DEPLOY.md).

### Phương án Cloud Run trước đây (tùy chọn, không dùng cho Community Cloud)

1. Đọc [audit source](docs/ARCHITECTURE_CURRENT.md); framework thật là Streamlit, không có build Node bắt buộc.
2. Owner hoàn tất [Project/Billing/bucket/IAM/IAP](docs/CLOUD_SETUP.md), repo private và phê duyệt. Không tự tạo tài nguyên hoặc upload dữ liệu thật khi thiếu phê duyệt.
3. Test local/container bằng dữ liệu giả, review và commit source. Theo [deploy và rollback](docs/DEPLOY_AND_ROLLBACK.md), chạy `scripts/deploy.sh` (PowerShell wrapper: `scripts/deploy.ps1`). Script build từ source allowlist của commit sạch, deploy đúng image digest, IAP bật ngay và không public.
4. Nghiệm thu staging qua IAP với 5 tài khoản, user bị từ chối, WebSocket/static assets, RAM/cold-start và publish/rollback trước production.

V1-A **web online chỉ đọc**: lọc, xem và export. Ingestion do Admin chạy CLI với ADC được Google xác minh email, có `ADMIN_EMAILS` và IAM riêng. Runtime account chỉ có quyền đọc bucket. Trang upload/role picker local không xuất hiện trong GCS mode; V1-B web-admin chưa triển khai.

Mặc định đề xuất Bangkok `asia-southeast3`, 1 vCPU/2 GiB, min 0/max 1, concurrency20, timeout1800s, request-based. Phải xác minh dịch vụ/region và tải thực tế; không cam kết phí cố định hay coi max1 là khóa publish. Bảng giá/budget và vận hành nằm trong tài liệu.

## Cập nhật dữ liệu — preview rồi xác nhận

Đọc [runbook](docs/OPERATIONS_RUNBOOK.md) để thiết lập ADC/GCS thật. Sau đây là ví dụ **local, dữ liệu giả**:

```sh
python scripts/generate_demo_data.py --rows 100000 --output data/generated
python scripts/ingest_excel.py preview --file data/generated/inventory_demo.xlsx \
  --classification data/generated/classification_demo.xlsx --data-date 2026-10-15
# Đọc KPI/delta/warnings và đối chiếu file, ngày chốt, version, generation.
python scripts/ingest_excel.py publish --version VERSION_ID --confirm CONFIRMATION_TOKEN --expected-generation GENERATION
python scripts/ingest_excel.py list
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION
# Chỉ sau khi xem đúng preview rollback:
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION --confirm TOKEN --expected-generation GENERATION
```

`VERSION_ID`, token và generation là giá trị lấy từ preview, không chạy nguyên placeholder. Preview không tự publish. Token gắn với đúng snapshot, checksum manifest và generation hiện hành; dữ liệu đổi giữa hai thao tác sẽ báo conflict, không cưỡng bức ghi đè.

Mỗi file là bản chụp toàn bộ, không cộng chồng. `.xlsx` được kiểm tra ZIP/XML/dung lượng/schema/kiểu/số lượng/ngày trước khi chuẩn hóa. File có công thức, macro, external link, DDE hoặc nội dung nhúng bị chặn; xuất bản values-only để ingest, bản gốc vẫn giữ trong staging. File lỗi không đổi active. Ngưỡng warning tùy chỉnh qua `VALIDATION_WARNING_LIMITS`, không tự deduplicate/sửa giá trị. Chi tiết schema/đơn vị nằm trong audit và runbook.

Raw inventory + classification gốc, Parquet, mapping, validation/metrics/manifest là snapshot bất biến. `metadata/active.json` được CAS **sau cùng**, sau readback/checksum/schema; generation chống publish đồng thời. Audit event chain giữ publication/rollback kể cả tiến trình dừng sau CAS. Dữ liệu từ ngày chốt trước vẫn truy vết được; code rollback và data rollback độc lập.

Dashboard mặc định đọc đúng active pointer (kể cả sau rollback). Chọn ngày trước active cho xem bản công bố gần nhất không vượt ngày chọn; ngày từ active trở đi vẫn dùng bản chính thức hiện hành. Xem lại các version khác trong lịch sử local/CLI. Cache gắn với namespace + version + ngày hiện tại; fragment kiểm tra active/generation và ngày mới mỗi30s. Đọc dashboard dùng Parquet, không parse Excel hoặc ghép phân loại lại mỗi lượt xem.

## KPI và dữ liệu không đổi định nghĩa

- `Số mã CCDC đang sử dụng`: Mã VTTB duy nhất không rỗng trên bản ghi Đang sử dụng.
- `Số mã vật tư chưa có ngày đưa vào sử dụng`: mã duy nhất thuộc Vật tư với ô ngày nguồn trống; không kết luận tất cả chưa sử dụng.
- `Tỷ lệ bản ghi thiết bị hỏng`: bản ghi Thiết bị/Hỏng chia cho toàn bộ bản ghi Thiết bị sau lọc; không cộng Số lượng khác ĐVT. Mẫu số0 hiển thị `—`.
- Chi nhánh ưu tiên `Chi nhánh mới`; đơn vị ưu tiên cột Đơn vị quản lý thứ2. Giữ cả cột gốc với tiền tố `Gốc · ` và số dòng Excel. Ghép tên chỉ đúng sau case/whitespace, xung đột cần rà soát, không nhân dòng.
- Tuổi là **tháng tròn tới hôm nay Asia/Ho_Chi_Minh**, độc lập ngày chốt. Ngày thiếu/lỗi/tương lai có tuổi Chưa xác định; cập nhật theo ngày mới không tải lại Excel.
- Bộ lọc chi nhánh, đơn vị phụ thuộc, loại (có Chưa phân loại), tình trạng, ngày chốt, tìm kiếm và chỉ báo chất lượng dùng chung cho KPI/bảng/biểu đồ/export. Đếm mã duy nhất trong từng nhóm có thể trùng giữa nhóm.
- Chi tiết phân trang 50/100/200/500 dòng, chọn thêm cột; CSV/Excel xuất toàn bộ kết quả sau lọc và có hai ngày. Mã/serial giữ chuỗi trong Excel; nhập CSV vào Excel cần chọn kiểu Text cho mã để không mất số0 đầu.

Đổi nhãn/card tại `ui_labels.py`; đổi giao diện tại `app.py`. Công thức ở `core.py` dùng chung local/cloud. Không đổi công thức chỉ để sửa tiêu đề.

## Cấu trúc chính

```text
app.py / ui_labels.py          Giao diện Streamlit và nhãn
core.py                        KPI, bộ lọc, tháng tròn, chuẩn hóa và export
services/config.py             Cấu hình local/staging/production fail closed
services/auth.py               IAP JWT + Admin ADC identity
services/storage.py            Local SQLite object adapter / GCS ADC
services/versions.py            Immutable snapshots, checksum, CAS và rollback
services/repository.py         Đọc Parquet, chọn active/history, cập nhật tuổi
services/validation.py          XLSX package/schema safety
services/transform.py           Chuẩn hóa một lần, Parquet, preview/delta
services/demo.py               Generator dữ liệu giả
scripts/ingest_excel.py         Ingestion V1-A, list, publish, rollback
scripts/deploy.sh / deploy.ps1  Deploy source commit → digest → private IAP
scripts/benchmark_ingestion.py  Benchmark 100k giả
assets/                        Demo HTML offline riêng, không nằm trong image
Dockerfile / .dockerignore     Streamlit non-root đúng PORT, context allowlist
```

HTML offline vẫn có thể tạo bằng `python scripts/build_html.py` → `dist/dashboard_ccdc.html`, chỉ nhúng dữ liệu giả. Nó không đồng bộ bucket hoặc thay thế phiên IAP online. Không đưa HTML/ZIP nhúng dữ liệu nội bộ vào Git/image.

SQLite runtime cũ của demo vẫn giữ tại `data/runtime` trên máy local, không tự migrate hoặc upload; bản online mới dùng `data/runtime-v2` hoặc GCS theo cấu hình. Các file thật của baseline đã được sao lưu local ngoài checkout trước khi loại khỏi tree mới. **Lịch sử commit `c591728` vẫn có dữ liệu thật**; owner cần xử lý quyền truy cập/private/history theo quy định. Không tự force-push hoặc khẳng định đã xóa khỏi lịch sử.

## Kiểm thử

```sh
python -m unittest discover -s tests -v
python scripts/test_deploy_safety.py  # toàn bộ gcloud được mock
python scripts/benchmark_ingestion.py --rows 100000 --output /tmp/ccdc-benchmark.json
```

Node chỉ cần cho test browser tùy chọn:

```sh
npm ci
npx playwright install chromium
python scripts/build_html.py
npm run test:browser
```

Có thể đặt `CHROMIUM_PATH` trỏ Chrome/Chromium đã cài. Để lặp lại5 phiên thật trên snapshot giả:

```sh
python scripts/prepare_load_fixture.py --rows 100000 --data-dir /tmp/ccdc-load/store --output /tmp/ccdc-load/expected.json
APP_ENV=local STORAGE_BACKEND=local LOCAL_DATA_DIR=/tmp/ccdc-load/store python -m streamlit run app.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
# Terminal khác, PID là PID tiến trình Streamlit vừa khởi động (tùy chọn để lấy RSS Linux):
node scripts/check_concurrency.cjs --origin http://127.0.0.1:8502 --pid PID --expected /tmp/ccdc-load/expected.json --output /tmp/ccdc-load/result.json
```

Fixture script chỉ tạo local giả và từ chối ghi đè store/output đã có. Harness cần5 chi nhánh và ít nhất2 trang mỗi nhánh; dùng100k như ví dụ. Server test do người chạy dừng khi xong. Benchmark và local browser không chứng minh IAM/IAP hoặc5 tài khoản thật trên Cloud Run đã hoạt động. Xem [kết quả và giới hạn](docs/VALIDATION_RESULTS.md).

## Troubleshooting

- Không có dữ liệu cloud: kiểm active pointer và quyền runtime đọc bucket; không fallback sang Excel laptop.
- Không đăng nhập: kiểm IAP/OAuth/danh sách user và expected audience; không tắt IAP hay tin header email để sửa nhanh.
- CLI từ chối Admin: kiểm ADC scope email + cloud-platform, Google verified email và `ADMIN_EMAILS` + IAM; không truyền `--actor` tự khai.
- Publish conflict: đọc lại active, tạo preview mới và đối chiếu; không ghi đè bỏ generation.
- File có formula/link: xuất file values-only và kiểm tra với Admin. Không sửa dữ liệu nguồn hoặc tắt validation.
- Lỗi storage: UI có mã sự cố, không hiển thị stack trace/token/dòng nguồn; kiểm Cloud Logging theo mã.
- Chi phí/cold start/WebSocket: xem runbook, request-based vẫn có thể tính phí khi giữ tab; Budget Alert không tự cắt phí.

Nếu dùng Codex, mỗi task đã cô lập: dùng checkout hiện có, không tự tạo Git worktree trừ khi được yêu cầu.
