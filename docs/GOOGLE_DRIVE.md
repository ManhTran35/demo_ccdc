# Nguồn Google Drive cho Streamlit Community Cloud

## Cấu trúc đã thống nhất

```text
Data/
├── Ton_CCDC_13102026.xlsx
├── Ton_CCDC_15102026.xlsx
└── Ton_CCDC_20102026.xlsx
```

Tên file là **`Ton_CCDC_ddmmyyyy.xlsx`**, phần ngày gồm **8 chữ số**.
`Ton_CCDC_01022026.xlsx` chốt ngày **01/02/2026**, không phải 02/01/2026.
Mỗi file là **một bản chụp toàn bộ**, không cộng dồn các file. Giữ file cũ trên
Drive nếu muốn xem lại lịch sử. Không thay đổi template cột của file hiện trạng.

Link Data đã cấu hình sẵn: thư mục ID `1iqFTwprrjOM587-pbF4gsivh-2lnDI2D`.
Link tham chiếu đã cấu hình sẵn: file ID `1VSXLhu2Iovyyq11r0HlGOFfjCG3txfJ4`.
Nếu link thư mục trỏ vào thư mục cha, ứng dụng tìm thư mục con tên `Data`; nếu
link trỏ trực tiếp vào Data thì đọc ngay. Có thể bật/tắt đọc thư mục con bằng
`recursive`. Không tải thư mục shortcut hoặc liên kết bên ngoài.

Dashboard chọn bản có **ngày trong tên lớn nhất không vượt hôm nay Việt Nam**.
Không lấy ngày upload/modified làm ngày chốt. Nếu có nhiều file cùng ngày,
file được sửa gần nhất được ưu tiên. Tên không khớp quy tắc hoặc có ngày không
tồn tại bị bỏ qua; nếu không còn bản hợp lệ, app báo lỗi để sửa. Chọn ngày cũ trên
dashboard sẽ lấy file gần nhất không vượt ngày chọn, không đổi bản hiện hành
của người xem khác. Không suy ra biến động nhập/xuất từ chênh lệch hai bản tồn.

## Thiết lập một lần

1. Trong Google Cloud Console, bật **Google Drive API** cho project quản lý
   service account. Chỉ cần quyền đọc Drive; không cần Cloud Run, bucket hay
   Billing của phương án triển khai trước.
2. Dùng service account dành riêng cho dashboard. Chia sẻ thư mục **Data** và
   **file tham chiếu** cho `client_email` của service account, quyền **Viewer**.
   Nếu chính sách Google Workspace chặn chia sẻ, quản trị viên cần cấp quyền
   theo chính sách của đơn vị; không cần công khai Drive.
3. Trong Streamlit Community Cloud, đặt quyền xem ứng dụng cho những người
   được cấp quyền trước khi bật nguồn dữ liệu nội bộ. Quyền Viewer trên Drive
   của service account không thay thế quyền xem app Streamlit.
4. Mở **App settings → Secrets**, dán mẫu
   [.streamlit/secrets.example.toml](../.streamlit/secrets.example.toml).
   Dùng bảng **`[drive]`**, điền `folder_id` và dán **nguyên JSON** vào
   `service_account_json` giữa ba dấu nháy đơn. Giữ nguyên `\n` trong private key.
   Không gửi khóa qua chat hoặc đưa file JSON/Secrets vào Git.
5. Lưu Secrets, reboot app nếu cần. Giữ entry point **`streamlit_app.py`**, nhánh
   **`deploy/streamlit-community`**, Python **3.12**.

Chỉ cần có `[drive]` là `streamlit_app.py` tự chọn nguồn Drive, không cần thêm
`data_source`. `folder_id` và `service_account_json` là hai trường bắt buộc;
`reference_file_id`, `data_subfolder`, `recursive` là tùy chọn. Mẫu đã có ID
Data và tham chiếu bạn cung cấp. Khi bỏ `reference_file_id`, app giữ file tham
chiếu đã dùng ở phiên bản trước, bảo toàn phân loại và KPI.

Secrets tối thiểu có cấu trúc:

```toml
[drive]
folder_id = "ID_THU_MUC_DATA"
service_account_json = '''
{ "type": "service_account", "client_email": "...", "private_key": "...", "token_uri": "https://oauth2.googleapis.com/token" }
'''
```

Thay phần JSON minh họa bằng JSON đầy đủ tải từ Google, không dùng nguyên dấu
`...`. Có thể dùng bảng TOML `[drive.service_account_json]` thay chuỗi JSON.
Không cần cấp quyền ghi Drive hoặc quyền truy cập toàn bộ Google Drive cá nhân:
chỉ chia sẻ Data và file tham chiếu cho service account quyền Viewer.

Để thử local, tạo `.streamlit/secrets.toml` từ mẫu và chạy
`python -m streamlit run streamlit_app.py`. Nếu không có Secrets, mặc định là
demo có nhãn rõ ràng. Khi có `[drive]` mà thiếu quyền/cấu hình, app báo lỗi,
không đổi sang dữ liệu giả. Có thể đặt `data_source = "demo"` ở root để chủ
động xem demo. Secrets cũ `[google_drive]` + `[gcp_service_account]` vẫn đọc được
để thuận tiện chuyển đổi. Luồng mới không tự lấy ADC hoặc `.env` thay Secrets.

## Cập nhật file hằng kỳ

Upload file `Ton_CCDC_ddmmyyyy.xlsx` hoàn chỉnh vào Data. Dashboard kiểm tra
metadata mỗi **30 phút**, hoặc khi bấm **Làm mới dữ liệu**. Chỉ khi revision của
file hiện trạng hoặc file tham chiếu đổi, ứng dụng mới tải và biến đổi lại.
`services/drive_loader.py` dùng `st.cache_data(ttl=1800)`, lưu danh sách file và
bytes Excel trong RAM. Cache download tối đa hai file, không pickle Service
Account/credentials hoặc tạo một DataFrame lớn cho từng người xem. Cache tách
theo account/nguồn/file/revision; nút làm mới chỉ xóa cache tương ứng. Metadata
được đọc trực tiếp để kiểm tra revision sau tải, tránh công bố file đang đổi.
Google Sheets gốc được export qua Drive API thành XLSX; file XLSX đang hiển thị
trong giao diện Google Sheets được tải dưới dạng XLSX. Google có giới hạn
export đối với Sheets gốc; lỗi export được báo và không đổi bản hiện hành.

Nguồn mới qua kiểm tra ZIP/XML, dung lượng, template, số lượng và ngày; đọc lại
metadata để tránh trộn file đang upload. Khi hợp lệ mới thay snapshot đang xem.
File có công thức/defined names/links không được bộ kiểm tra chấp nhận: dùng
bản **chỉ chứa giá trị** để bảo đảm không đọc cached formula thiếu hoặc lỗi.
File tham chiếu giữ sheet `Chi_tiet` và template cột của `Phan_loai_VTTB.xlsx`.

Ngày trong ô Excel đọc **ngày/tháng/năm**: `01/02/2026` luôn là 1 tháng 2.
Hỗ trợ ô date Excel, serial Excel và chuỗi ngày kèm giờ. Ngày không tồn tại,
trống hoặc tương lai được đánh dấu để rà soát, không đoán theo tháng/ngày và
không biến thành 0 tháng. Bảng, ô chọn ngày và file CSV/Excel xuất ra hiển thị
`dd/mm/yyyy`; snapshot Parquet dùng ISO nội bộ để tính tuổi và so sánh ổn định.

Drive là nơi lưu file gốc lâu dài. SQLite, Parquet và lịch sử đã đọc trên máy
Streamlit chỉ là **cache**, có thể được tạo lại sau reboot. Việc đọc ngày cũ
tải bản chụp cũ từ Drive khi chưa có cache. Lỗi file mới/mạng/quyền giữ bản hợp
lệ đang có trong tiến trình; sau reboot mà không đọc được Drive thì báo lỗi,
không khẳng định còn một bản chính thức trên đĩa máy Streamlit.

## Kiểm tra và lỗi thường gặp

- Nhãn nguồn phải là **Google Drive**, không phải Dữ liệu mẫu. Kiểm tra tên file
  đang xem ở sidebar và ngày chốt trên đầu trang. Sidebar hiển thị ngày dữ liệu,
  thời gian sửa nguồn và lần đồng bộ gần nhất theo múi giờ Việt Nam; thời gian
  sửa file không thay thế ngày dữ liệu trong tên file.
- Không thấy file: kiểm tra tên đúng 8 chữ số, đuôi `.xlsx`, quyền Viewer và
  ngày không ở tương lai. File `.xls`, `.xlsm`, shortcut và file tạm `~$` bị bỏ qua.
- 401/403/404: kiểm tra Drive API, hai ID và chia sẻ **cả Data lẫn tham chiếu**
  cho đúng service account. Không bật public để sửa lỗi quyền.
- File sửa giữa lúc tải hoặc checksum đổi: đợi upload hoàn tất rồi làm mới.
- Dữ liệu sai: sửa hoặc loại bản sai tại nguồn Drive theo quy trình nội bộ;
  dashboard đọc lại theo ngày/revision. Không có chức năng ghi/xóa file Drive.

Các phép đọc được kiểm thử tự động bằng HTTP giả lập và Excel được tạo trong
test. Tại thời điểm triển khai mã, môi trường Codex bị proxy chặn hai liên kết
Drive (403), chưa có credential Drive dùng được, nên **chưa xác minh đọc hai
file thật**. Không coi kết quả test mô phỏng là kết nối thật đã thành công.

## Local trước, Git Push sau

```sh
python -m unittest discover -s tests -v
python scripts/benchmark_drive_loader.py --rows 100000 --output /tmp/ccdc-drive-benchmark.json
git diff --check
git status --short
```

Sau khi kiểm thử đạt, review thay đổi rồi commit/push source lên nhánh
`deploy/streamlit-community`. Không thêm Service Account JSON hoặc file Secrets
thật vào staging; `.gitignore` chặn `*.json`, `**/secrets.toml`, các Secrets biến
thể và thư mục `credentials/`. Mẫu `secrets.example.toml` chỉ có placeholder.

Streamlit Community Cloud Free không cần Docker, database hoặc Drive SDK riêng.
Ứng dụng dùng `google-auth[requests]` với Drive API chỉ đọc. Để hạn chế RAM:
chuẩn hóa Excel một lần, đọc Parquet cho UI, cache một DataFrame đã chuẩn hóa,
phân trang bảng và xuất theo bộ lọc hiện có. Cache máy Streamlit có thể mất sau
reboot; file gốc và lịch sử vẫn nằm trên Drive. Kết quả 100.000 dòng local được
ghi trong [báo cáo rà soát/kiểm thử](DRIVE_LOADER_REVIEW.md); khả năng thực tế còn phụ thuộc workbook và tài
nguyên Community Cloud cấp cho app.
