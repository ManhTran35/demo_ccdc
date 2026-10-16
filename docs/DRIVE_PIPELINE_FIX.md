# Chẩn đoán và sửa pipeline Drive — 16/10/2026

## Cơ chế trước sửa và nguyên nhân

Rà soát nhánh `deploy/streamlit-community`, baseline `90f9841`. Người dùng báo
app đang chạy và đã cấu hình Drive. Phiên Codex không có Secrets của app hoặc
quyền truy cập nội dung Drive thật; không khẳng định đã kiểm tra file live.

| Mã nguồn trước sửa | Cơ chế/điểm lỗi |
|---|---|
| `streamlit_app.py` | Có `[drive]` thì chọn `community-drive`; entry point khác có thể chọn chế độ khác. |
| `services/drive_loader.py` | Service Account từ Secrets; Drive API chỉ đọc, cache danh sách/bytes 1.800 giây. |
| `services/drive.py` | Đã tự tải, validate và gọi `VersionManager.prepare_version/commit`; không cần ingestion CLI trong chế độ Drive. |
| `services/versions.py`, `services/config.py` | Snapshot/active pointer ở SQLite `/tmp` của tiến trình Community; không phải kho publish bền vững. |
| `services/validation.py` | Từ chối chung mọi `f`, `formula*`, `definedName`; không phân biệt công thức ô và metadata; lỗi không nêu file/sheet/ô. |
| `app.py: refresh_state/main` | Mọi `last_error` đều bị gắn nhãn giữ bản cũ; nhánh không có active còn dùng thông báo Admin/CLI của luồng GCS. |

Lần đồng bộ đầu thất bại đặt lịch kiểm tra sau 30 phút rồi ném lỗi. Lần rerun
trong thời gian này trả về sớm, không ném lại; không có active nhưng fragment
vẫn báo giữ bản cũ. `main()` tiếp tục báo chưa publish. Hai thông báo mâu thuẫn
không chứng minh hệ thống đang đọc một kho snapshot cũ thành công.

Kiểm tra trực tiếp XML của **bản đính kèm**, không đọc/evaluate cached values:

- `file ccdc.xlsx`: `Sheet1`, **91.955 công thức ô**, tại `J2:J91956`;
  có `_xlnm._FilterDatabase` trỏ vùng tĩnh `Sheet1!$A$1:$AG$1`.
- `Phan_loai_VTTB.xlsx`: `Chi_tiet`, công thức conditional formatting ở
  `A7:J4459`, dropdown ở `H7:H4459` và dropdown extension. Không có công thức
  ô dữ liệu. Bộ kiểm tra cũ dừng ngay ở metadata định dạng của file tham chiếu,
  vốn được parse trước file tồn, và che mất nguyên nhân tiếp theo ở cột J.
- Sau sửa, tham chiếu qua validation và parse được **4.453 dòng** theo pipeline
  cũ. File tồn vẫn bị chặn chính xác tại **`Sheet1!J2`**. Nếu Drive chứa cùng
  nội dung, cần bản tồn values-only do người quản lý dữ liệu xuất tại nguồn.

## Luồng sau sửa

```text
Drive Data + file tham chiếu (nguồn bền vững)
  → chọn tên/ngày/revision → download cache 30 phút
  → kiểm tra ZIP/XML → template/nghiệp vụ hiện có → Pandas → Parquet
  → DriveSnapshotCache tạm → dashboard hiện có
```

`services/drive_cache.py` tách hoàn toàn khỏi staging/publish/confirmation token
của `VersionManager`. Chỉ đổi con trỏ cache sau khi materialize đủ dữ liệu;
snapshot đọc lại được đối chiếu SHA-256. Không ghi lên Drive, không yêu cầu
script hoặc một dịch vụ lưu trữ khác trên Community Cloud. Luồng publish
local/GCS cũ không thay đổi; GCS giữ kho công bố bền vững riêng.

- Cold start kiểm tra từ mới đến cũ cho tới file tồn hợp lệ đầu tiên. Lỗi file
  mới vẫn hiển thị nếu phục hồi từ file cũ. Lỗi kết nối không bị diễn giải thành
  file dữ liệu không hợp lệ để tự chọn một file khác.
- Nếu đã có cache hợp lệ, lỗi nguồn mới giữ đúng snapshot/mapping đang xem.
  Nếu không có, báo trạng thái rỗng, không có KPI giả hoặc thông báo giữ bản cũ.
  Nút làm mới có mặt cả khi lần tải đầu thất bại; retry trong TTL vẫn giữ lỗi.
- Reference lỗi chặn cả cặp nguồn; không lấy mapping cũ để hợp thức hóa dữ liệu
  mới. Sau reboot cần Drive truy cập được, lịch sử tồn còn giữ và reference
  hiện tại hợp lệ. Không có bản last-good độc lập khi Drive không truy cập được.
- Công thức ô, kể cả sheet ẩn, luôn bị từ chối trước `core.read_table`.
  Conditional formatting/dropdown không được thực thi hoặc dùng làm giá trị.
  Chỉ whitelist ba defined names filter/print tích hợp với vùng tĩnh nội bộ;
  biểu thức động/custom names, external links, macro/DDE/OLE vẫn bị chặn.
- Log có download/validation/process/activate/error với file, sheet, ô và mã
  lỗi; không ghi credentials, nội dung ô, công thức hoặc response body của API.
- Cache danh sách và bytes TTL 30 phút; fingerprint gồm revision hai file và
  revision pipeline. Đổi bộ lọc không parse lại Excel. Mapping tham chiếu được
  tái dùng trong tiến trình khi revision không đổi; DataFrame lớn chỉ cache một
  bản. Refresh bỏ cache tải của đúng nguồn, metadata sau download luôn đọc mới.

`core.py`, theme, nhãn KPI, layout biểu đồ/bảng, bộ lọc, date parsing và export
không thay đổi. Thay đổi UI chỉ ở trạng thái nguồn lỗi/rỗng và hướng dẫn thử lại.

## Bằng chứng local trước Git Push

- **208 tests đạt** trên Python **3.12.14** và **3.14.8**. Kiểm tra cả cold
  recovery sau mất cache, lỗi reference có công thức, không gọi publish cũ,
  partial write giữ active cũ, TTL/retry, mã/serial/ngày và KPI/bộ lọc/export.
- Chromium + Streamlit thật trên Python 3.14, API giả lập: lỗi đầu/rerun rỗng,
  refresh thành công, file mới lỗi giữ đúng KPI, mất mạng giữ bản đang xem,
  file hợp lệ mới kích hoạt đúng ngày; **0 lỗi JavaScript**.
- 100.000 dòng giả, XLSX 9.613.538 bytes: cold validate/process/cache **46,98 s**,
  warm index/bytes **0,0018 s**, UI tổng quan **0,79 s**, lọc **0,078 s**;
  xuất 10.000 dòng CSV/XLSX **1,06 s**, RSS đỉnh **669,50 MiB**. Một lần list và
  hai lần download API trong benchmark. Không cam kết số đo trên máy Cloud.
- Dependency check đạt trên cả hai Python; `git diff --check` đạt.

Lệnh tái kiểm tra:

```sh
python -m unittest discover -s tests -v
python scripts/benchmark_drive_loader.py --rows 100000 --output /tmp/ccdc-benchmark.json
git diff --check
```

Log, benchmark JSON và ảnh kiểm tra local ở
`/workspace/outputs/ccdc_pipeline_fix/`. API trong test là giả lập; kiểm tra file
đính kèm được thực hiện riêng và không đưa dữ liệu đó vào Git. Chưa nghiệm thu
URL live hoặc Secrets của app. Hướng dẫn cập nhật nằm trong
[Google Drive](GOOGLE_DRIVE.md) và [Streamlit](STREAMLIT_DEPLOY.md).

Sau khi Community Cloud lấy commit mới, kiểm tra app trỏ đúng nhánh
`deploy/streamlit-community`, main file `streamlit_app.py`; giữ Secrets `[drive]`
hiện có. Có thể **Manage app → Reboot**, rồi **Làm mới dữ liệu**. Với lỗi công
thức ô dữ liệu thật, xuất bản values-only từ Excel sau khi kiểm tra kết quả,
upload `Ton_CCDC_ddmmyyyy.xlsx` hoàn chỉnh và làm mới. Không dùng cached formula
values trong ứng dụng để bỏ qua validation.
