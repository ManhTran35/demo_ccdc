# Kết quả kiểm chứng V1-A — 15/10/2026

Đã hoàn thiện phần mã nguồn và kiểm thử local bằng dữ liệu giả. **Chưa tạo tài nguyên, build trên Cloud Build, deploy staging/production hoặc cấp quyền IAP thật.** Không có URL online để nghiệm thu ở thời điểm này.

## Nghiệp vụ và tự động hóa

| Phần kiểm tra | Kết quả |
|---|---|
| Python nghiệp vụ cũ + auth/config/storage/versions/ingestion/UI | 150 test đạt, không skip |
| Deploy-script safety | 8 kịch bản đạt; mọi lệnh gcloud đều mock, không tạo tài nguyên |
| Demo HTML offline bằng dữ liệu giả | 23 nhóm kiểm tra browser đạt; không có request ra ngoài loopback |
| Dependency integrity | `pip check` đạt; lock đầy đủ; không tắt TLS/chữ ký/checksum |
| Build/upload context | Official `gcloud meta list-files-for-upload` chỉ thấy 16 file code/config được allowlist, không data/Excel/HTML/ZIP/.env/.git |
| Container local | Build offline + runtime/UI/export/non-root/PORT/fail-closed đạt; production recipe online bị DNS bridge chặn |

Test giữ nguyên KPI, mã chuỗi/số0 đầu, tháng tròn tại 11/12,35/36,59/60,71/72,95/96, ngày thiếu/lỗi/tương lai và ngày mới. UI test xác nhận bộ lọc đơn vị phụ thuộc, xóa lựa chọn không còn hợp lệ khi version đổi, cùng tập dữ liệu cho KPI/export, chuyển active/rollback làm cập nhật mặc định, không parse/normalize Excel khi xem Parquet. Các cột gốc, số dòng và cảnh báo không bị tự xóa/nhân bản.

Storage test gồm SQLite CAS qua nhiều tiến trình, fake-GCS với generation, checksum/readback/schema, upload đứt, race hai publisher, pointer write thất lạc phản hồi, manifest bị sửa, rollback và phân biệt orphan chuẩn bị với publication thành công. Thứ tự publication lấy từ audit chain, không phụ thuộc UUID hoặc độ chính xác timestamp. Phân loại Excel gốc được sao chép bất biến vào raw của version và kiểm checksum.

Auth test dùng chữ ký **ES256 thật được sinh trong test** và transport public-key giả: từ chối chữ ký sai, audience/issuer sai, hết hạn, thiếu token và header email mạo danh. CLI chỉ dùng email verified từ ADC + Admin allowlist; không tin `--actor` hoặc email do client tự gửi. GCS web không có role picker/upload/admin và không fallback local khi lỗi. Đây không phải kiểm tra IAP/IAM của một dịch vụ đã deploy.

Các log test nằm trong thư mục output bàn giao; có warning Streamlit bare-mode/deprecation từ dependency pin và warning ZipFile của fixture member trùng cố ý. Không có test failed, expected-failure hoặc skip trong kết quả cuối.

## Benchmark 100.000 dòng giả — một tiến trình ingestion

Chạy `scripts/benchmark_ingestion.py --rows 100000` với Python3.12.14 trên Linux, không dùng file nội bộ:

| Chỉ tiêu | Đo được |
|---|---:|
| XLSX / Parquet | 9.613.539 / 4.251.128 byte |
| Dòng / cột chuẩn hóa | 100.000 / 42 (gồm20 cột gốc) |
| Kiểm tra ZIP/XML | 15,597 giây |
| Parse Excel | 37,720 giây |
| Chuẩn hóa/phân loại | 3,955 giây |
| Ghi Parquet | 0,564 giây |
| Tổng ingestion (không tính sinh file) | 58,360 giây |
| Đọc Parquet | 0,284 giây |
| Cập nhật riêng tuổi/ngày | 0,309 giây |
| KPI / lọc kết hợp+tìm kiếm | 0,193 / 0,034 giây |
| Xuất đủ100k CSV / Excel | 0,930 / 12,909 giây |
| Peak RSS toàn tiến trình | 745,66 MiB |

Peak gồm sinh file giả, ingestion, readback và export; không cộng máy móc các phase-memory với nhau. Một pipeline riêng đã stage/validate/transform/prepare/readback/commit thật vào LocalStorageAdapter để phục vụ thử browser; active generation1, ngày dữ liệu15/10/2026. Không lấy zero-test hoặc open-port làm bằng chứng dữ liệu hoạt động.

## Năm phiên trình duyệt đồng thời

Chạy `scripts/check_concurrency.cjs` với 5 browser context độc lập trên chính snapshot 100.000 dòng qua Streamlit thật, không dùng stub DataFrame:

- Cả5 phiên đạt ba KPI toàn bộ, sau đó mỗi phiên lọc một chi nhánh khác nhau (CN01–CN05), mỗi chi nhánh10.000 dòng.
- Kiểm tra KPI theo nhánh, thêm cột Serial, trang2/100, tải đủ10.000 dòng CSV và Excel ở mỗi phiên, ngày dữ liệu và kiểu chuỗi của mã/serial.
- Quay lại Tổng quan vẫn giữ đúng filter riêng; không lẫn state giữa người dùng và không có page exception.
- Tổng31,684 giây; mở đầu8,12–8,32 giây, đổi filter4,15–4,70 giây, phân trang2,91–4,41 giây, chuẩn bị export đồng thời8,70–9,93 giây.
- Peak RSS Streamlit do kernel ghi nhận **1.027,49 MiB**, thấp hơn2GiB trong lần chạy này. Server kiểm thử riêng đã dừng.

Máy local có4CPU khả dụng, không bị giới hạn như Cloud Run1vCPU. Kết quả này **không chứng minh** latency/cold start/chi phí/5 danh tính thật qua IAP. Cần staging test trên cấu hình đề xuất trước khi chốt2GiB/concurrency20.

## Container và toolchain

Docker daemon28.4.0 đã có. Build production recipe ban đầu lấy được base image chính thức nhưng pip trong build container lỗi DNS. Đã giữ nguyên TLS và không đổi checksum; hướng kiểm chứng local là bộ wheel exact-lock tải qua TLS rồi cài offline trong context tạm chỉ chứa source allowlist. Production Dockerfile/deploy không chứa wheelhouse hoặc dữ liệu thật. Kết quả cuối **đạt**, image local `sha256:6a0db3e1dd8682d54295354f3b4224a91526c2baaf687e435e39aefffd26ac30`. Bộ57 wheel được kiểm SHA-256 từng file với release metadata chính thức PyPI, sau đó build bằng `--network=none`, cài `--no-index --require-hashes`. Context/recipe offline chỉ ở thư mục tooling, production Dockerfile giữ bước pip online. Không tắt TLS hoặc sửa expected hash.

Runtime đã kiểm UID/GID10001, source root-owned chỉ đọc đối với runtime user, PID1 exec Python, `PORT=9090`, giới hạn1CPU/2GiB và health200. Chromium xác minh KPI dữ liệu giả1.000 dòng `200 / 11 / 3,501%`, lọc CN01 còn100 dòng, Chi tiết và CSV/Excel xuất đủ100 dòng đúng nhánh, mã chuỗi giữ số0 đầu. Container staging thiếu cấu hình hiển thị lỗi an toàn, không có KPI hoặc fallback local. Source hash image khớp code đã kiểm; `/app` không chứa Excel, dữ liệu nội bộ, `.git`, `.env` hoặc credential.

Kiểm thử đã phát hiện/sửa quyền đọc file COPY (mode0600 trên máy làm UID10001 không import được); Dockerfile hiện có `chmod -R a+rX /app` trước chuyển USER. Health200 một mình không phát hiện lỗi này, nên đã dùng UI/export thật để nghiệm thu. Hai container thử đã được dừng/xóa. Đây là smoke1.000 dòng trong container1CPU; phép đo5 phiên100k ở phần trên là host local4CPU. **Chưa có bằng chứng deploy Cloud Run hoặc IAP thật.**

Google Cloud CLI589.0.0 lấy từ `dl.google.com`, SHA-256 từng component kiểm theo manifest chính thức. Đã xác minh help cho native `--iap`, `--no-allow-unauthenticated`, `--invoker-iam-check`, request-based CPU và các cờ IAP/cloud-run resource. Chưa có `pwsh`; PowerShell wrapper chỉ kiểm tĩnh, Bash và8 guard tests đã chạy.

## Những gì còn chờ để triển khai thật

- ProjectID, region được duyệt, Billing, quyền tạo/quản lý tài nguyên; runtime/build/deployer/Admin account theo least privilege.
- Loại danh tính và danh sách5 user, Admin publish, cấu hình OAuth/IAP và expected audience của dịch vụ thật.
- Phê duyệt Google Cloud của đơn vị/CNTT/ATTT; staging chỉ dữ liệu giả, production chỉ sau nghiệm thu và phê duyệt dữ liệu.
- Có binding `GOOGLE_APPLICATION_CREDENTIALS` và file readable trong máy, nhưng `google.auth.default()` trả `DefaultCredentialsError`; chưa có ADC Google dùng được hoặc xác minh IAM dự án. Không yêu cầu key/token trong chat; owner dùng login ADC an toàn theo tài liệu.
- Repository phải private/được phê duyệt; chưa xác minh được visibility qua API. Tree mới loại Excel thật và artifact nhúng dữ liệu sau khi sao lưu local ngoài checkout, **lịch sử `c591728` vẫn chứa chúng**. Owner quyết định xử lý quyền/history; chưa force-push hoặc đẩy bản nâng cấp lên remote chưa được xác minh.
- Endpoint tài liệu `docs.cloud.google.com`/`cloud.google.com` bị egress403 trong máy. Native CLI flags đã kiểm bằng SDK chính thức; expected audience cụ thể, IAM, IAP browser/WebSocket, storage thật và billing phải kiểm trên project được duyệt.

## Checklist nghiệm thu

- [x] Giao diện Streamlit, bộ lọc, KPI/bảng/biểu đồ và CSV/Excel giữ chức năng.
- [x] Local/GCS adapter, Parquet, cảnh báo/schema, raw bất biến, publish/rollback/CAS và readback có test.
- [x] Không parse Excel mỗi lượt xem; tuổi cập nhật độc lập ngày chốt.
- [x] Cập nhật dữ liệu lỗi không đổi active; nhiều phiên một ngày không ghi đè; rollback data không đổi code.
- [x] Quy trình code/deploy/rollback và runbook vận hành có tài liệu.
- [x] Container local non-root/PORT/UI/export/fail-closed đạt qua build offline exact-wheel; production build online cần DNS của runner phù hợp.
- [ ] Repository private và lịch sử Git cũ được owner xử lý theo chính sách.
- [ ] Staging thật qua IAP với5 user được duyệt và user bị từ chối; WebSocket/static assets/cold start/RAM thực.
- [ ] Phê duyệt dữ liệu, production go-live, logging/budget và nghiệm thu owner.
