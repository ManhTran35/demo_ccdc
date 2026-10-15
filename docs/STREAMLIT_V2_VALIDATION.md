# Kiểm thử UI, ngày và Google Drive — 15/10/2026

Nhánh triển khai: `deploy/streamlit-community`; entry point `streamlit_app.py`.

## Đã kiểm tra

- **182 Python unittest**: nghiệp vụ cũ, lưu phiên bản, auth/GCS, ngày,
  Google Drive HTTP giả lập, pipeline Excel/Parquet và UI AppTest.
- Tên `Ton_CCDC_01022026.xlsx` → **01/02/2026**; chọn snapshot theo ngày trong tên,
  bỏ bản tương lai; bản mới thay toàn bộ số dòng. Đổi revision tham chiếu tạo
  lại mapping. Xem ngày cũ không đổi active pointer chung.
- Năm luồng đồng bộ đồng thời chỉ tải một cặp inventory/reference và công bố
  một snapshot. Metadata không đổi thì không tải/parse Excel lại.
- Lỗi Excel mới, mất quyền/mạng, metadata đổi giữa lúc tải giữ bản đã xác minh;
  lần đầu lỗi không dùng dữ liệu giả thay Drive. HTTP 401/403/404/429/500,
  checksum, giới hạn tải, pagination thiếu/lặp và giới hạn duyệt thư mục đều có test.
- UI chạy thật trong Chromium ở **1500px** và **390px**: theme đỏ Viettel Post,
  ba KPI, date input `13/10/2026`, lọc CN01 → **100/1.000** dòng, trang Chi tiết,
  download CSV/Excel, không có Admin/upload trong mode online; không lỗi JS hoặc
  tràn ngang trang trên điện thoại. Biểu đồ nhóm tuổi giữ thứ tự từ dưới 12 tháng
  đến từ 96 tháng; biểu đồ ngang giúp đọc nhãn trên màn hình nhỏ.
- File CSV và Excel tải qua trình duyệt được đọc lại: **100 dòng sau lọc**, ngày
  chốt `13/10/2026`, ngày tính tuổi `15/10/2026`; ngày sử dụng hiển thị dd/mm/yyyy.
- Đối chiếu **5.000 dòng đầu của Excel gốc đã đính kèm**: 4.653 ô ngày dạng chuỗi
  đọc hợp lệ, 347 ô trống; 1.362 giá trị dễ nhầm ngày/tháng đã được khẳng định
  đọc đúng ngày trước tháng. Không in mã tài sản/nhân viên hoặc đưa dữ liệu thật
  vào Git. Đây là mẫu kiểm tra, không phải kiểm định toàn bộ workbook.
- `pip check`, `git diff --check` và đọc mẫu Secrets TOML hợp lệ.

Lệnh test:

```sh
python -m unittest discover -s tests -v
```

Ảnh, log và kết quả browser của phiên làm việc được lưu ngoài repository tại
`/workspace/outputs/ccdc_streamlit_v2/`; không có file nguồn nội bộ trong commit.

## Chưa xác minh trực tiếp

Hai URL Drive thực tế bị proxy môi trường Codex trả **403**; Google ADC hiện có
không dùng được và chưa có service account Drive của ứng dụng. Kết quả Drive là
test với HTTP giả lập và Excel tạo trong test, **không phải bằng chứng đã đọc
thành công thư mục hoặc file tham chiếu thật**.

Các miền Drive/API/OAuth đã được thêm vào bản nháp network của môi trường; bản
nháp cần lưu/publish mới có hiệu lực. Phần này độc lập với Secrets của ứng dụng
Streamlit. Không có URL app Streamlit do người dùng triển khai để kiểm tra live
ở phiên này. Sau cấu hình [Google Drive](GOOGLE_DRIVE.md), cần kiểm tra trên app
thật: nhãn Google Drive, tên file, ngày chốt và KPI đối chiếu Excel nguồn.
