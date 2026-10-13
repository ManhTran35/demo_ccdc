# Dashboard theo dõi tồn CCDC · Demo local

Có hai cách sử dụng:

1. **HTML độc lập**: tải [dashboard_ccdc.html](downloads/dashboard_ccdc.html), mở trực tiếp bằng Chrome/Edge/Firefox bản mới. Không cần Python, không cần Internet. File đã nhúng đầy đủ 91.955 dòng, bảng phân loại và nguyên file Excel mẫu; dung lượng khoảng 15 MB.
2. **Streamlit**: tải [mã nguồn ZIP](downloads/ma_nguon_ccdc.zip), giải nén rồi chạy theo hướng dẫn dưới đây, hoặc clone repository này. Dữ liệu/lịch sử lưu trên ổ đĩa; phù hợp để chỉnh sửa và phát triển tiếp.

Không triển khai Cloud Run, không kết nối dịch vụ cloud. Hai bản dùng cùng dữ liệu mẫu và quy tắc nghiệp vụ, **không đồng bộ lịch sử** với nhau. Các file nguồn ứng dụng được nêu trong yêu cầu chưa tồn tại trong checkout ban đầu, nên demo này được tạo mới sau khi kiểm tra hai file Excel.

## Chạy Streamlit trên Windows

Cài **Python 3.12** (có thể dùng Python 3.13 nếu thư viện có wheel phù hợp). Mở Terminal/CMD tại thư mục `demo_ccdc` sau khi giải nén:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m streamlit run app.py --browser.gatherUsageStats false
```

Không cần kích hoạt virtualenv. Streamlit sẽ in địa chỉ local và mở trình duyệt trên máy của bạn. Dừng bằng `Ctrl+C`. Lần mở đầu cần khoảng 20–35 giây để đọc Excel và tạo phiên mẫu; lần sau dùng bản chụp đã lưu.

Trên macOS/Linux:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false
```

Thư viện chính: Streamlit 1.50.0, pandas 2.3.3, openpyxl 3.1.5, XlsxWriter 3.2.9. `requirements-lock.txt` khóa toàn bộ dependency đã cài kiểm thử; `requirements.txt` liệt kê bốn thư viện chính. HTML dùng SheetJS CE 0.18.5 được nhúng sẵn; license nằm ở `assets/XLSX-LICENSE.txt`, không cần npm khi sử dụng.

## Dữ liệu mẫu và quy tắc

- `data/samples/ccdc.xlsx`: nguyên file `file ccdc.xlsx`, sheet `Sheet1`, 91.955 bản ghi và 20 cột.
- `data/samples/classification.xlsx`: nguyên file `Phan_loai_VTTB.xlsx`, sheet `Chi_tiet`, tiêu đề tại dòng 6, 4.453 dòng tham chiếu.
- Ngày chốt mẫu **13/10/2026**, đã được người dùng xác nhận. Phiên mẫu được tạo sẵn để xem dashboard; các file nhập tiếp theo cần xem trước rồi duyệt.
- `Chi nhánh` dùng `Chi nhánh mới`; dùng cột `Chi nhánh` nếu file mới có tên chuẩn này. Không tự lấy `Chi nhánh cũ` thay cho hiện trạng mới.
- Hai cột `Đơn vị quản lý` được giữ riêng. Bộ lọc dùng cột thứ hai (`Đơn vị quản lý [2]`), lấy cột thứ nhất nếu ô thứ hai trống. Trong file mẫu hai cột giống nhau ở mọi dòng.
- Các cột gốc có tiền tố `Gốc · ` trong bảng chi tiết; cột không tên giữ thành `Gốc · Cột 11 (không tên)`. Không loại bỏ cột hoặc dòng nghi trùng. Số dòng Excel gốc được giữ để đối chiếu.
- Ghép tên chính xác sau chuẩn hóa khoảng trắng và chữ hoa/thường. Tham chiếu trùng cùng loại được gom về một ánh xạ; tham chiếu có loại/nhóm xung đột được gán `Chưa phân loại`, đánh dấu cần rà soát. Không nhân bản dòng nguồn. Tên gần giống chỉ là gợi ý.
- Tuổi sử dụng tính theo **ngày hiện tại tại Asia/Ho_Chi_Minh**. Tháng tròn = chênh lệch tháng, trừ 1 nếu chưa tới ngày kỷ niệm trong tháng. Ngày thiếu, lỗi hoặc tương lai có tuổi `Chưa xác định`. Nhóm tuổi: dưới 12; 12–35; 36–59; 60–71; 72–95; từ 96 tháng. Cache Streamlit chứa ngày hiện tại; fragment kiểm tra ngày mới mỗi 30 giây và chạy lại dashboard khi qua ngày. HTML cũng kiểm tra mỗi 30 giây và khi quay lại tab, không cần tải lại Excel.
- Ngày nguồn hỗ trợ `dd/mm/yyyy`, `dd-mm-yyyy`, ISO `yyyy-mm-dd`, ô ngày Excel và serial ngày Excel. Số lượng phải là số hữu hạn không âm (số thập phân dùng dấu chấm); ô trống/không phải số/âm chặn công bố.

## Bộ lọc và chỉ tiêu

Chi nhánh, đơn vị quản lý phụ thuộc chi nhánh, loại (có `Chưa phân loại`), tình trạng, ngày chốt. Không chọn giá trị = tất cả. Tìm kiếm mã/tên/serial/đơn vị và chỉ báo chất lượng cũng là bộ lọc dùng chung; mọi KPI, bảng, biểu đồ và file xuất sử dụng đúng tập dữ liệu đó.

Chọn ngày chốt lấy bản công bố có ngày chốt gần nhất **không vượt ngày chọn**; nếu cùng ngày chốt, lấy lần công bố mới nhất. Trước ngày chốt đầu tiên sẽ báo chưa có dữ liệu, không dùng phiên tương lai. Mặc định mở phiên được công bố mới nhất. Khôi phục tạo lần công bố mới của bản chụp cũ, giữ ngày chốt và không xóa lịch sử; bộ lọc mặc định chuyển về ngày chốt của bản được khôi phục.

KPI:

- **Số mã CCDC đang sử dụng**: `Mã VTTB` duy nhất, không rỗng, trên dòng `Đang sử dụng`.
- **Số mã vật tư chưa có ngày đưa vào sử dụng**: mã duy nhất thuộc `Vật tư` với ô ngày nguồn để trống. Không coi ngày lỗi/tương lai là thiếu ngày, không kết luận vật tư chưa sử dụng.
- **Tỷ lệ bản ghi thiết bị hỏng**: số bản ghi `Thiết bị` có `Tình trạng = Hỏng` / toàn bộ bản ghi `Thiết bị` sau lọc. Mẫu số bằng 0 hiển thị `—`. Không cộng Số lượng/ĐVT; tooltip ghi tử số, mẫu số và đơn vị.

Bảng chi nhánh/tình trạng đếm mã duy nhất **trong từng nhóm**. Một mã có thể xuất hiện ở nhiều nhóm, không ép tổng nhóm bằng mã duy nhất toàn bộ. Biểu đồ tuổi đếm **bản ghi** `Đang sử dụng` có ngày hợp lệ, ghi số bị loại vì ngày chưa xác định.

Chi tiết phân trang 50/100/200/500 dòng, chọn thêm các cột gốc. CSV/Excel xuất **toàn bộ** bản ghi sau lọc, không chỉ trang hiện tại, có cột ngày chốt và ngày tính tuổi. Mã/serial giữ chuỗi trong Excel. CSV không có kiểu cột: khi Excel nhập CSV hãy chọn Text cho mã/serial để Excel không tự đổi `0007` thành `7`.

## Nhập, duyệt và phân loại

1. Vào Quản trị, chọn vai trò `Người nhập` hoặc `Người duyệt` (mô phỏng).
2. Chọn ngày chốt và file `.xlsx` theo mẫu, tối đa 25 MB. HTML hỗ trợ tối đa 250.000 dòng và 200 cột mỗi sheet.
3. Bấm kiểm tra/xem trước: lưu nguyên file gốc, kiểm tra cấu trúc, số lượng, ngày, mã, serial và phân loại. Số lượng lỗi/cột thiếu chặn công bố. Các cảnh báo ngày/phân loại/serial không tự xóa bản ghi.
4. Chỉ vai trò `Người duyệt` mới có thể công bố. File lỗi và bản xem trước không thay đổi phiên hiện hành.
5. Lịch sử có xem lại, tải nguyên file gốc và khôi phục. Mỗi phiên là bản chụp **toàn bộ**, không cộng với file các kỳ trước.
6. Quản lý phân loại bằng nhập bảng Excel có các cột `Tên VTTB gốc`, `Loại VTTB`, `Nhóm lớn`, `Trạng thái rà soát`; `Ghi chú phân loại` tùy chọn. Xem trước rồi người duyệt lưu cho lần nhập tiếp theo. Phiên đã công bố giữ nguyên phân loại; muốn áp dụng lại thì nhập và duyệt file hiện trạng mới.

**Vai trò chỉ mô phỏng, không phải xác thực hay phân quyền bảo mật.** Không có tài khoản, mật khẩu hoặc dịch vụ người dùng. Lịch sử Streamlit là SQLite + snapshot gzip tại `data/runtime`, nguyên file gốc tại `data/runtime/originals`; sao lưu cả thư mục khi cần. File lỗi được giữ nguyên ở thư mục originals ngay cả khi không tạo được phiên.

HTML lưu lịch sử, bảng phân loại thay thế và nguyên file nhập trong IndexedDB của trình duyệt. Dữ liệu mẫu nằm trong HTML, có thể xem lại kể cả khi không cho lưu trữ; nếu IndexedDB bị chặn, nhập/lưu phiên sẽ báo lỗi. Đổi trình duyệt, di chuyển HTML hoặc xóa dữ liệu trình duyệt có thể mất lịch sử. Trong Lịch sử có xuất/nhập file sao lưu `.json.gz`, gồm các bản chụp, phân loại và nguyên file gốc. Chưa có cơ chế nhiều người duyệt, đồng bộ hoặc khóa giao dịch nhiều người dùng.

## Chỉnh sửa mã nguồn

```text
app.py                         Giao diện Streamlit
core.py                        Chuẩn hóa, tuổi, phân loại, KPI, lọc, xuất, phiên
assets/dashboard.template.html Cấu trúc HTML
assets/dashboard.css           Giao diện HTML
assets/dashboard.js            Nghiệp vụ và tương tác HTML offline
assets/xlsx.full.min.js         SheetJS CE, nhúng trong HTML
scripts/build_html.py          Tạo lại một file HTML độc lập
scripts/prepare.py             Tạo dữ liệu mẫu trên ổ đĩa và kiểm tra
tests/test_core.py             Kiểm thử lõi Python
tests/browser.cjs              Kiểm thử trình duyệt và xuất/nhập Excel
data/samples/                  Hai file Excel gốc
```

Sau khi sửa HTML/CSS/JavaScript hoặc file mẫu:

```bat
.venv\Scripts\python.exe scripts\build_html.py
```

Kết quả ở `dist/dashboard_ccdc.html`; nhúng thư viện Excel và dữ liệu nên file này tự mở offline. Thay dữ liệu mẫu cần cập nhật `SAMPLE_CUTOFF` trong `core.py`. Thay file mẫu không ghi đè lịch sử Streamlit đang có; dùng luồng nhập/duyệt để thay hiện trạng.

## Kiểm thử

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Kiểm thử browser tùy chọn cần Node.js, Chromium/Chrome hoặc Chromium của Playwright:

```sh
npm ci
npx playwright install chromium
python scripts/build_html.py
npm run test:browser
```

Có thể đặt `CHROMIUM_PATH` trỏ tới Chrome/Chromium đã cài. Bộ kiểm thử tự mở một máy chủ tạm trên loopback, kiểm tra trình duyệt, rồi dừng; không triển khai website. Xem `TEST_REPORT.md` để biết kết quả thực tế, thống kê mẫu và giới hạn kiểm thử.

Nếu dùng môi trường Codex, mỗi task đã được cô lập: dùng checkout hiện có, không tự tạo Git worktree trừ khi được yêu cầu.
