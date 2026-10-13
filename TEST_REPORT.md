# Kết quả kiểm thử · 13/10/2026

Kiểm thử tại Linux, Python 3.12, Streamlit 1.50.0, Chromium headless. Ngày chốt mẫu và ngày tính tuổi trong lần chạy này đều là **13/10/2026**, nhưng được xử lý độc lập; tuổi dùng múi giờ Việt Nam.

## Dữ liệu thật

| Kiểm tra | Kết quả |
|---|---:|
| Dòng nguồn / sau ghép phân loại | 91.955 / 91.955 |
| Mã VTTB duy nhất toàn bộ | 4.454 |
| Mã VTTB đang sử dụng | 3.911 |
| Mã vật tư thiếu ngày sử dụng | 577 |
| Bản ghi thiết bị / bản ghi thiết bị hỏng | 58.290 / 20 |
| Tỷ lệ bản ghi thiết bị hỏng | 0,034311% |
| Dòng thiếu ngày sử dụng | 7.117 |
| Ngày không đọc được / ngày tương lai | 0 / 12 |
| Dòng có serial nghi trùng / serial nghi trùng độc nhất | 339 / 167 |
| Dòng nghi giống nhau, bỏ STT khi đối chiếu | 11.869 |
| Tên chưa phân loại / dòng chưa phân loại | 0 / 0 |
| Tên cần rà soát / dòng ảnh hưởng | 12 / 13 |
| Số lượng lỗi / thiếu mã / thiếu tên | 0 / 0 / 0 |

Hai file trong `data/samples` có SHA-256 giống nguyên file đính kèm. Không sửa dữ liệu, không tự loại dòng nghi trùng. Thống kê nghi trùng chỉ là chỉ báo cần rà soát, không khẳng định các dòng đều là lỗi.

## Các kiểm tra đã chạy đạt

- **21 test Python**, không có test bị bỏ qua. Kiểm tra tháng tròn 11/12, 35/36, 59/60, 71/72, 95/96; ngày thiếu, lỗi, tương lai; đổi ngày tính tuổi; ghép chính xác; tham chiếu trùng/xung đột; giữ raw và số dòng; số lượng lỗi; cột bắt buộc; KPI theo bản ghi/mã; bộ lọc và xuất cùng tập dữ liệu; mã/serial/mã đơn vị có số 0 đầu; vai trò duyệt; file lỗi không đổi phiên; chọn phiên theo ngày và khôi phục giữ lịch sử; danh sách tên rà soát gom theo tên chuẩn hóa.
- **23 nhóm kiểm tra trình duyệt HTML**, không có nhóm bị bỏ qua: dữ liệu thật/KPI khớp Python, mốc tuổi, không lấy phiên tương lai, bộ lọc chi nhánh và đơn vị phụ thuộc, tình trạng và biểu đồ tuổi rỗng, chỉ báo chất lượng, phân trang, tìm kiếm, CSV/Excel 521 dòng sau lọc, **Excel đủ 91.955 dòng**, nhập phân loại tham chiếu trùng, nhập/xem trước/công bố, chặn vai trò người nhập, giữ mã `0007` dưới kiểu chuỗi, tuổi chưa xác định, chặn file thiếu cột/số lượng lỗi, tải nguyên file gốc giống từng byte, khôi phục, lưu qua reload, sao lưu nén và nhập vào trình duyệt mới. Không ghi nhận lỗi JavaScript hoặc request HTTP ra ngoài loopback.
- **Streamlit AppTest**: Tổng quan, ba KPI, Chi tiết, Quản trị, lọc chi nhánh, đổi chi nhánh làm bỏ đơn vị không còn phù hợp, chọn ngày trước phiên đầu không lấy dữ liệu tương lai. Kiểm tra fragment cập nhật ngày không gây lỗi render. Test tuổi trước/sau ngày mới nằm trong bộ test Python.
- **Dịch vụ Streamlit thật**: đã khởi động, endpoint health trả `ok`; Chromium mở được Tổng quan với KPI `3.911`, `577`, `0,034%` và chuyển sang Chi tiết thành công. Không dùng health/PID làm bằng chứng duy nhất.
- **Cài đặt lặp lại**: chạy đầy đủ `scripts/install_cloud.sh` với dependency khóa; giữ nguyên phiên mẫu và file gốc; chuẩn bị snapshot và 21 test chạy đạt. Dữ liệu runtime của bản Streamlit bàn giao vẫn chỉ có phiên mẫu, không chứa các file kiểm thử browser.
- HTML khoảng **15,14 MiB**. Mở và chuẩn hóa 91.955 dòng khoảng **3–4 giây** trên máy kiểm thử; thời gian trên máy cá nhân tùy CPU, RAM và trình duyệt. Excel nhiều dòng tạo trong worker, giao diện không phải tải tất cả dòng vào bảng DOM.

## Giới hạn kiểm thử và mô phỏng

- HTML được chạy qua máy chủ loopback trong kiểm thử. Điều hướng `file://` trực tiếp bị chính sách Chromium của máy kiểm thử chặn, nên đường mở trực tiếp bằng double-click chưa được xác minh trên máy này. File HTML tự chứa dữ liệu, JavaScript, CSS, thư viện Excel và file gốc; không có dependency CDN. Hướng dẫn sử dụng là tải file về và mở bằng trình duyệt desktop hiện đại; nếu trình duyệt quản trị chặn file local thì dùng bản Streamlit.
- Hướng dẫn Windows đã được cung cấp; không có máy Windows để thực thi trực tiếp. Các lệnh không cần kích hoạt virtualenv.
- Vai trò là mô phỏng tự chọn, không có đăng nhập/phân quyền bảo mật. Một máy local, không có quy trình nhiều người duyệt đồng thời.
- Lịch sử HTML là IndexedDB, Streamlit là SQLite/file local; không đồng bộ. HTML có sao lưu/nhập lại lịch sử. Chưa kết nối dịch vụ cloud hoặc triển khai bên ngoài.
- Chưa chờ qua một mốc nửa đêm thật. Các kiểm thử đổi ngày chứng minh cách tính/cache; fragment Streamlit và timer HTML kiểm tra ngày mới mỗi 30 giây, HTML còn kiểm tra khi quay lại tab.
- Chưa xác minh khôi phục môi trường trong task Codex mới hoặc publication của môi trường. `install_script` và `start_skill` đã lưu vào bản nháp; việc lưu không tự publish.

Lệnh tái chạy nằm trong README. Kiểm thử browser tạo file tạm, dùng trình duyệt riêng và dọn sau khi hoàn tất; không sửa dữ liệu nguồn.
