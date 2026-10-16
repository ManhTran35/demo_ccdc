# Sổ tay vận hành CCDC V1-A

Web staging/production chỉ đọc. Viewer xem/lọc/export qua IAP; Admin cập nhật dữ liệu hoặc rollback bằng `scripts/ingest_excel.py` trên máy/Cloud Shell được duyệt. Đổi code và đổi dữ liệu là hai quy trình độc lập. Chưa xác minh quyền cloud/phê duyệt dữ liệu cho triển khai trong phiên bàn giao này; điền owner, liên hệ hỗ trợ, project và năm user vào sổ vận hành private của đơn vị trước go-live.

## Cập nhật dữ liệu mỗi tuần

1. Kiểm tra môi trường đích: `APP_ENV`, project, bucket/prefix, ngày dữ liệu và danh tính ADC. Production cần phê duyệt dữ liệu theo CLOUD_SETUP. Không dump env/token hoặc dùng tên actor tự khai. Admin phải vừa nằm trong `ADMIN_EMAILS` vừa có IAM phù hợp.
2. Nhận file `.xlsx` inventory và classification từ nguồn được duyệt. Giữ bản gốc, kiểm đúng kỳ/ngày chốt. Không bỏ dòng trùng, sửa số lượng/ngày hoặc đổi định nghĩa KPI để qua validation. Khóa thiết bị vật lý chưa được xác nhận nên duplicate serial/code chỉ dùng để cảnh báo, không tự deduplicate.
3. Chạy preview. Lần đầu cần file phân loại; lần sau không truyền `--classification` thì dùng mapping active đã có. File phân loại mới phải review ảnh hưởng KPI/phân loại cùng lúc với inventory.

```bash
python scripts/ingest_excel.py preview --file /approved/path/inventory.xlsx \
  --classification /approved/path/classification.xlsx --data-date 2026-10-15
```

4. Đọc JSON preview: `version`, `confirmation_token`, `expected_generation`, `validation`, `comparison`. Đối chiếu số dòng, đơn vị, mã, mã đang dùng, vật tư thiếu ngày, tỷ lệ bản ghi thiết bị hỏng và chênh lệch kỳ trước. Bất thường/warning phải được Admin hiểu và chấp nhận trước publish; lỗi nghiêm trọng không publish. Ngày ví dụ không thay cho ngày chốt thật.
5. Khi quyết định duyệt đúng preview đó, copy nguyên ba giá trị vào lệnh sau. Không có chế độ tự publish mặc định hoặc token tĩnh:

```bash
python scripts/ingest_excel.py publish --version VERSION \
  --confirm CONFIRMATION_TOKEN --expected-generation EXPECTED_GENERATION
```

6. Chờ CLI xác nhận thành công, refresh dashboard qua IAP. Kiểm tra active version/ngày chốt, tổng dòng và KPI, một số dòng mẫu đã biết và export theo filter. Ghi người duyệt, thời gian, version ID, ngày dữ liệu và kết quả đối chiếu vào sổ vận hành private. Không đưa raw Excel hoặc nội dung bản ghi vào log hỗ trợ.

Preview đã stage, validate, transform sang snapshot Parquet và kiểm tra checksum/schema trước publish; **chưa thay active**. Publish ghi `metadata/active.json` sau cùng bằng compare-and-swap generation. Version dùng timestamp UTC + UUID để nhiều lần trong ngày không đè nhau. Dashboard cache gắn version/ngày tuổi, không parse Excel ~100.000 dòng trên mỗi request. `max-instances=1` không thay cơ chế khóa này.

### Warning và giới hạn

`MAX_UPLOAD_MB` giới hạn file nén; `MAX_UNCOMPRESSED_MB`, `MAX_ROWS`, `MAX_COLUMNS` giới hạn workbook giải nén/kích thước bảng. `VALIDATION_WARNING_LIMITS` là JSON cấu hình tỷ lệ cảnh báo trong `services/transform.py`; dùng tên rule được code hỗ trợ, review thay đổi và chạy test trước áp dụng. Không tăng giới hạn khi chưa đo RAM/thời gian và kiểm tra nguồn file. V1-A không đưa file lớn qua HTTP Cloud Run; không có trang upload web và không có signed URL mặc định.

File có formula, macro, external link, workbook lỗi, giả đuôi, cấu trúc ZIP không an toàn hoặc thiếu sheet/cột bắt buộc sẽ bị chặn theo validator. Sửa template tại nguồn được duyệt và tạo preview mới. Không tắt validation, checksum, TLS hoặc package verification để thông qua.

## Đọc lịch sử và khôi phục

```bash
python scripts/ingest_excel.py list
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION
# Review preview; then confirm exactly this generation/token:
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION \
  --confirm TOKEN --expected-generation GENERATION
```

Rollback kiểm tra snapshot rồi đổi pointer; không xóa bản lỗi và không đổi code. Nếu một Admin khác đã publish sau preview, generation conflict là cơ chế bảo vệ: đọc lại list/preview, đối chiếu việc đang thực hiện và chỉ xác nhận sau quyết định mới. Không sửa trực tiếp `active.json` trong Console để vượt CAS. Cần phục hồi từ sự cố storage nghiêm trọng thì owner phối hợp Admin, dùng generation/checksum đã ghi và giữ evidence.

## Nghiệp vụ cần đối chiếu sau publish

- Mã đang sử dụng = số mã khác rỗng duy nhất trên bản ghi “Đang sử dụng”. Mã VTTB là mã loại/vật tư, không đồng nghĩa một thiết bị vật lý.
- “Mã vật tư thiếu ngày” phản ánh ô ngày nguồn trống theo logic hiện tại, không tự đổi thành “chưa sử dụng”.
- Tỷ lệ hỏng = số bản ghi Thiết bị + Hỏng / số bản ghi Thiết bị sau bộ lọc. Không cộng số lượng khác đơn vị đo.
- Tuổi tháng tròn tính đến ngày hiện tại theo Asia/Ho_Chi_Minh, độc lập ngày chốt. Ngày thiếu/lỗi/tương lai ở nhóm chưa xác định.
- Tất cả KPI, bảng/biểu đồ và export cùng nhận filter. Không suy ra nhập/xuất kho từ chênh snapshot tuần.

## Xử lý sự cố

| Hiện tượng | Kiểm tra và hành động |
|---|---|
| Preview/validation lỗi | Đọc mã lỗi/report, kiểm file/sheet/cột, giữ bản gốc; sửa tại nguồn và preview lại. Active cũ giữ nguyên. |
| Quá giới hạn hoặc parse chậm | Không tăng cấu hình ngay. Đo file nén/giải nén/dòng/RAM, kiểm ZIP bất thường, cân nhắc giới hạn được duyệt. File oversize không được đọc toàn bộ; bản gốc local còn nguyên. |
| Upload/build mất mạng | Kiểm staging/build record; active không đổi trước publish hoàn tất. Chẩn đoán mạng/quyền rồi tạo preview mới khi cần, không lặp publish mù. |
| Publish/rollback generation conflict | Admin khác có thể đã đổi active. `list`, preview lại, trao đổi người vận hành và xác nhận quyết định mới; không force overwrite. |
| Không có active version | Môi trường mới chưa ingest/publish; Admin tạo preview hợp lệ. Nếu từng có dữ liệu, owner kiểm quyền/object history; không seed dữ liệu thật local làm fallback. |
| Web báo lỗi đọc dữ liệu | Lấy mã theo dõi/thời gian, kiểm Run Logs và quyền Object Viewer của runtime, bucket/prefix, active/manifest/schema/checksum. Không lộ stack trace hoặc nội dung dữ liệu cho Viewer. |
| Admin CLI bị từ chối | Kiểm ADC userinfo scopes, email verified, `ADMIN_EMAILS`, IAM/quota project. gcloud login và ADC khác nhau. Không truyền email tự khai hoặc tải service-account key để né kiểm tra. |
| User không đăng nhập được | Kiểm danh tính, IAP membership/OAuth, service-agent invoker và JWT audience/cert network. Không tắt IAP hoặc cấp public Run/bucket để sửa. |
| WebSocket ngắt sau một lúc | Reconnect/refresh, đối chiếu timeout 1800s và proxy. Publish nằm ở CLI nên không phụ thuộc session web. |
| Quá RAM/CPU hoặc export chậm | Kiểm peak RAM/concurrency, phiên export lớn và cold start; benchmark lại trước tăng RAM/concurrency/max instances. Không tự thêm SQL/VM. |
| Dữ liệu mới sai | Admin rollback data bằng preview/token/generation; giữ revision code. |
| Revision mới lỗi | Deployer rollback traffic về revision có record tốt; giữ active data. Kiểm schema compatibility trước chuyển traffic. |

## Theo dõi thường kỳ

Sau mỗi publish: Admin đối chiếu version/ngày/KPI và export. Hằng tuần: owner kiểm Cloud Run Logs, lỗi storage/auth, latency, CPU/RAM và request/WebSocket. Hằng tháng: Billing thực tế cho Run, Storage, Build, Artifact Registry và network; Budget Alert không phải hard cap. Năm user không tạo chi phí cố định; tab Streamlit/WebSocket giữ mở có thể kéo dài thời gian tính phí. Min instances 0 có cold start.

Hằng quý và khi thay nhân sự: rà IAP viewer membership, Admin allowlist, IAM data/build/runtime/deployer, OAuth consent và máy giữ ADC; thu hồi quyền đã hết nhu cầu. Đổi label UI tại `ui_labels.py`, không sửa công thức KPI để đổi tên card/chart; test rồi deploy code theo DEPLOY_AND_ROLLBACK.

Bucket retention/lifecycle do owner duyệt. Giữ raw, Parquet, mapping, manifest, report và audit liên quan cho phiên cần truy vết. **Không xóa staging đang được version tham chiếu**, bao gồm classification gốc nếu manifest/report còn trỏ vào staging. Không đặt quy tắc tự xóa toàn bộ staging theo tuổi trước khi xác định không có tham chiếu. Không xóa active version hoặc image/revision tốt cuối cùng trong job dọn dẹp. Chỉ dọn orphan/staging không tham chiếu sau kiểm tra và retention được duyệt.

## Bàn giao và evidence

Lưu private: thông tin owner/on-call, phê duyệt dữ liệu, danh sách user, Project ID/region/bucket (không secret), lịch sử release commit/digest/revision, version dữ liệu, kết quả staging/IAP/cloud performance, quyết định rollback và cấu hình retention/budget. Không lưu token, password, raw Excel hoặc credential JSON vào Git/issues/logs. Xem [CLOUD_SETUP.md](CLOUD_SETUP.md) về endpoint mạng và quyền; xem [DEPLOY_AND_ROLLBACK.md](DEPLOY_AND_ROLLBACK.md) về lệnh triển khai/khôi phục.
