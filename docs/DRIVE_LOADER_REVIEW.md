# Rà soát và tích hợp Google Drive cá nhân

## Luồng giữ nguyên

| Thành phần | Vai trò và kết luận |
|---|---|
| `streamlit_app.py` | Entry point Community Cloud; tự bật Drive khi có `[drive]` trong Secrets. |
| `app.py`, `ui_labels.py`, `services/presentation.py` | Giữ layout, nhãn, KPI, bảng, biểu đồ, bộ lọc và export; bổ sung caption nguồn và thời gian. |
| `core.py` | Đọc ô Excel, giữ cột trùng/mã dạng chuỗi, chuẩn hóa, tuổi tháng tròn, KPI và export; không sửa công thức hoặc mã nguồn file này. |
| `services/validation.py`, `services/transform.py` | Giữ kiểm tra Excel và pipeline Pandas/Parquet hiện tại; không thay bằng `pd.read_excel` có thể đổi tên các cột trùng hoặc mất số 0 đầu. |
| `services/repository.py`, `storage.py`, `versions.py` | Giữ snapshot, checksum, CAS và lịch sử; dữ liệu máy Community Cloud chỉ là cache. |
| `services/drive.py` | Drive REST chỉ đọc, chọn tên/ngày hợp lệ, kiểm tra revision sau tải, giữ bản tốt khi file/mạng lỗi. |
| `services/drive_loader.py` | Đọc Secrets, tạo Service Account client, cache dữ liệu 30 phút và nối vào repository cũ. |
| `services/auth.py`, CLI/Docker/GCS | Luồng Cloud Run cũ được giữ; không cần để chạy Community Cloud. |
| `assets/` | HTML offline riêng; không thay đổi để triển khai Drive trên Streamlit. |

## Cấu hình và cache

Nguồn chính đọc `st.secrets["drive"]["folder_id"]` và
`st.secrets["drive"]["service_account_json"]`. JSON có thể là chuỗi TOML nhiều
dòng hoặc bảng TOML; thiếu/sai cấu hình báo lỗi rõ ràng và không dùng dữ liệu
giả thay thế. Các cấu hình đã deploy kiểu cũ vẫn tương thích khi chuyển đổi.

Hai cache `st.cache_data(ttl=1800)` lưu danh sách file và bytes Excel, không lưu
Service Account hoặc client API. Cache tách theo digest của credentials/nguồn
và file/revision, không đưa secret thô vào key hoặc báo cáo. Download cache có
tối đa hai entry; DataFrame chuẩn hóa dùng `st.cache_resource(max_entries=1)`
để tránh giữ nhiều bản 100.000 dòng hoặc tạo bản sao cho mỗi người xem.

Nút Làm mới xóa cache đúng nguồn rồi đồng bộ; chỉ file thay đổi mới chuẩn hóa
lại. Kiểm tra revision sau download vẫn đọc metadata trực tiếp từ API. Ngày
chốt từ tên file, thời gian sửa nguồn và thời gian đồng bộ là ba thông tin riêng.
Resource của repository cũng được khóa theo môi trường và digest cấu hình để
không tái sử dụng nguồn demo/account cũ khi Secrets thay đổi. Ô ngày có identity
theo active pointer, sửa lỗi Chrome giữ ngày cũ sau refresh mặc dù bảng đã đổi.

## Kiểm chứng local

- Test Secrets JSON/TOML, lỗi an toàn, scope cách ly, TTL thật 1.800 giây bằng
  đồng hồ giả lập, cache hit, retry sau lỗi và refresh chọn file mới.
- **196 unittest đạt trên Python 3.12.14 và 3.14.8**, gồm toàn bộ nghiệp vụ,
  storage, auth, UI và loader/cache; không sửa công thức trong `core.py`.
- Chromium xác minh metadata, bộ lọc, export và refresh qua loader mới;
  API được giả lập, Excel được tạo trong test. Tải CSV/Excel rồi đọc lại đủ
  **100 dòng sau lọc**. Thêm file mới làm đổi cả tên file, ngày từ 13/10 sang
  14/10 và bảng từ 1.000 sang 8 dòng, không cộng dồn; không có lỗi JavaScript.
- `scripts/benchmark_drive_loader.py` chạy **100.000 dòng** qua bộ tải, validation,
  Pandas, Parquet và UI AppTest: cold **42,56 giây**, cache **0,0014 giây**, RAM
  đỉnh **664,79 MiB**, lọc CN01 và xuất **10.000 dòng**. Workbook giả 9,61 MB.

Đây là số đo một tiến trình local, không phải cam kết tài nguyên hoặc thời gian
của Community Cloud Free. Nguồn Drive gốc lưu lâu dài; cache máy Streamlit có
thể mất sau reboot. Chưa có Service Account thật trong phiên Codex để nghiệm thu
truy cập hai liên kết người dùng; kết quả mock không chứng minh kết nối thật.

Log/JSON/ảnh của phiên local nằm tại `/workspace/outputs/ccdc_drive_loader/`.
Thực hiện test local và review trước Git Push theo [hướng dẫn](GOOGLE_DRIVE.md).
