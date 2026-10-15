# Triển khai Streamlit Community Cloud

Theo yêu cầu mới của chủ dự án, host đích là **Streamlit Community Cloud**.
Không cần tạo Cloud Run, Billing hoặc IAP để chạy bản demo này.

## Cấu hình triển khai

| Trường | Giá trị |
|---|---|
| Repository | `ManhTran35/demo_ccdc` |
| Branch | `deploy/streamlit-community` |
| Main file path | `streamlit_app.py` |
| Python version (Advanced settings) | `3.12` |
| Dependencies | `requirements.txt` ở root, các phiên bản đã pin |
| Secrets | Theo [Google Drive](GOOGLE_DRIVE.md) để đọc dữ liệu thật; demo không cần |

Python **3.12** vẫn là bản khuyến nghị. Bản sửa lỗi import biểu đồ đã được kiểm
thử thêm trên **Python 3.14.8**. Dashboard dùng Vega-Lite trực tiếp, giữ biểu đồ
và thứ tự nhóm tuổi; không import schema Altair 5.5 gây lỗi `TypedDict(closed=...)`
trên Python 3.14. Nếu app đang chạy 3.14, cập nhật code rồi Reboot, không bắt buộc
đổi Python để xử lý lỗi này. Chi tiết kiểm chứng: [báo cáo V2](STREAMLIT_V2_VALIDATION.md).

[Mở màn hình triển khai đã điền repository/branch/entry point](https://share.streamlit.io/deploy?repository=ManhTran35%2Fdemo_ccdc&branch=deploy%2Fstreamlit-community&mainModule=streamlit_app.py).

Đăng nhập Streamlit Community Cloud bằng tài khoản có quyền truy cập repository,
chọn Python 3.12 trong Advanced settings rồi nhấn **Deploy**. Nếu giao diện không
nhận tham số URL, chọn Create app → Deploy a public app from GitHub và nhập bảng trên.
Tên miền `*.streamlit.app` chỉ được xác lập sau khi Community Cloud tạo app thành công;
URL mở màn hình triển khai ở trên không phải URL ứng dụng đang chạy.

## Hành vi của bản triển khai

`streamlit_app.py` dùng chung giao diện và công thức trong `app.py`/`core.py`,
với theme đỏ–trắng theo Viettel Post. Khi Secrets có `data_source = "google_drive"`,
app đọc thư mục **Data**, file **Ton_CCDC_ddmmyyyy.xlsx** và file tham chiếu qua
Drive API chỉ đọc. Xem [thiết lập Google Drive](GOOGLE_DRIVE.md). Nguồn Drive
được kiểm tra mỗi 5 phút hoặc khi bấm Làm mới dữ liệu; ngày chốt lấy từ tên file.

Khi chưa cấu hình nguồn Drive, app chọn `community-demo`: 1.000 dòng **dữ liệu giả**, chốt 13/10/2026,
không có trang Admin, upload hoặc vai trò tự chọn. Bộ lọc, KPI, bảng, biểu đồ và
xuất CSV/Excel vẫn hoạt động. Thư mục dữ liệu demo riêng nằm trong thư mục tạm,
không tái sử dụng `LOCAL_DATA_DIR` hoặc kho dữ liệu thật của local.

Filesystem của Community Cloud không phải kho dữ liệu lâu dài. Trong chế độ demo,
dữ liệu giả được tạo lại sau reboot. Trong chế độ Drive, file gốc nằm lâu dài ở
Drive, snapshot Parquet/SQLite là cache và được dựng lại từ nguồn khi cần.

Để dùng dữ liệu nội bộ thật cho 5 người, cần hoàn tất phê duyệt theo đặc tả,
quyền xem app và kho dữ liệu bền vững riêng. IAP của Cloud Run không áp dụng cho
Community Cloud; không đổi cấu hình thành local rồi công khai trang quản trị.
Luồng GCS/IAP trước đó vẫn có trong source để tham khảo, không phải cấu hình
của entry point Community Cloud.

## Cập nhật và kiểm tra

Push code mới vào `deploy/streamlit-community`; Community Cloud theo dõi nhánh
đã chọn. Nếu cần, dùng Manage app → Reboot. Trước khi báo đã triển khai, kiểm tra
trên URL do Streamlit cấp: ba KPI, nhãn đúng nguồn dữ liệu, bộ lọc và ngày dd/mm/yyyy;
với demo dữ liệu giả, lọc CN01 còn 100 dòng,
trang Chi tiết và hai nút tải CSV/Excel sau khi chuẩn bị xuất file. Không có
trang Admin hoặc ô upload trong bản online.

Kiểm tra tương đương tại máy phát triển:

```sh
python -m streamlit run streamlit_app.py
python -m unittest discover -s tests -p test_dashboard_smoke.py -v
```

Trong môi trường Codex hiện tại, Git HTTPS hoạt động, nhưng yêu cầu HTTPS tới
`share.streamlit.io` và `docs.streamlit.io` bị proxy trả 403. Không có công cụ
đăng nhập trình duyệt Streamlit được cấp cho phiên làm việc. Vì vậy việc push
source, kiểm thử và chuẩn bị link Deploy không chứng minh app đã được tạo;
cần phiên đăng nhập Community Cloud và truy cập mạng phù hợp để hoàn tất.

Lưu ý: repository hiện hiển thị Public trên GitHub và lịch sử commit `c591728`
đã chứa file thật từ lần upload trước. Nhánh mới không thêm file dữ liệu thật;
việc loại file khỏi tree hiện tại không xóa dữ liệu khỏi lịch sử Git.
