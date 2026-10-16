# Deploy, redeploy và rollback

Chưa deploy GCP trong phiên bàn giao. Các lệnh tạo build/revision chỉ chạy sau khi owner cung cấp tài nguyên, danh tính và phê duyệt trong [CLOUD_SETUP.md](CLOUD_SETUP.md). Kiểm thử local không thay thế kiểm thử IAP thực tế.

## 1. Kiểm thử container local

Giữ Streamlit là entry point; không có bước build frontend Node. Dockerfile dùng Python 3.12, lock dependencies, UID/GID `10001`, lắng nghe `0.0.0.0:$PORT` (mặc định 8080). `/tmp` chỉ là vùng tạm; không ghi dữ liệu production lên filesystem container. Build context dùng allowlist, không chứa `data/`, `assets/`, `downloads/`, `.git`, Excel, HTML nhúng dữ liệu hoặc `.env`.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python scripts/generate_demo_data.py --rows 100000 --output /tmp/ccdc-synthetic
APP_ENV=local STORAGE_BACKEND=local LOCAL_DATA_DIR=/tmp/ccdc-snapshot \
  python scripts/ingest_excel.py preview \
  --file /tmp/ccdc-synthetic/inventory_demo.xlsx \
  --classification /tmp/ccdc-synthetic/classification_demo.xlsx --data-date 2026-10-15
# Review JSON; copy the exact version/token/generation from this preview:
APP_ENV=local STORAGE_BACKEND=local LOCAL_DATA_DIR=/tmp/ccdc-snapshot \
  python scripts/ingest_excel.py publish --version VERSION --confirm TOKEN --expected-generation GENERATION
docker build -t ccdc-dashboard:local .
docker run --rm --name ccdc-local -p 127.0.0.1:8080:8080 \
  -e APP_ENV=local -e STORAGE_BACKEND=local -e LOCAL_DATA_DIR=/tmp/ccdc-local \
  ccdc-dashboard:local
```

`VERSION`, `TOKEN`, `GENERATION` là placeholder bắt buộc thay đúng kết quả preview, không phải chuỗi để copy chạy nguyên. Lệnh ingestion trên tạo snapshot 100.000 dòng cho host. Lệnh container không mount host: lần đầu app tự tạo mẫu giả 1.000 dòng tại `/tmp/ccdc-local`; dữ liệu này mất khi xóa container. Nếu muốn dùng snapshot host trong container local, chỉ mount thư mục dữ liệu giả riêng ở chế độ **read/write** và cấp quyền cho UID/GID 10001; LocalStorageAdapter dùng SQLite/journal nên mount read-only không phù hợp. Không đổi quyền hàng loạt thư mục dữ liệu thật để sửa test. Production chỉ đọc GCS, không mount thư mục local này. Mở `http://127.0.0.1:8080`, đối chiếu KPI, filter, bảng, CSV/Excel và `/_stcore/health`. Muốn kiểm tra `PORT` khác, dùng `-e PORT=9090 -p 127.0.0.1:9090:9090`.

App cloud sẽ từ chối cấu hình local trên Cloud Run. Local mô phỏng danh tính chỉ dùng để phát triển. Không dùng container local làm cách bỏ qua IAP production.

## 2. Chuẩn bị commit và tài nguyên đã duyệt

Chạy test Python/UI theo README, review diff, kiểm tra không có dữ liệu nội bộ/secret trong tree rồi commit/merge theo quy trình Git của đơn vị. Script yêu cầu working tree sạch. Nó dùng **đúng `HEAD` đã commit**, xuất `app.py`, `core.py`, `ui_labels.py`, `services/*.py`, lockfile, Dockerfile và hai ignore file bằng `git archive`. Không upload working tree hoặc Git history. Kiểm tra source diff vẫn cần thiết vì allowlist không phát hiện secret vô tình viết trong mã Python.

Cài official Google Cloud CLI và đăng nhập bằng tài khoản deployer được duyệt. `gcloud run deploy --help` phải có native `--iap`; SDK 589.0.0 đã được kiểm chứng cú pháp local trong tác vụ này. Script dừng nếu các cờ bảo mật cần thiết không có. Không dùng `--allow-unauthenticated`, `--no-iap`, `--no-invoker-iam-check` hoặc tắt TLS để đi tiếp.

Source/log buckets, Artifact Registry Docker repository, runtime account và build account phải **đã tồn tại**. Script dùng `gcloud builds submit` với source/log buckets và build account cụ thể, rồi deploy **image digest trả về từ build**. Cách này không yêu cầu Cloud Run source deploy tự tạo repository/bucket mặc định. Quyền tạo tài nguyên đã được owner thực hiện trong bước setup, tách khỏi deploy code.

## 3. Deploy staging với dữ liệu giả

Export biến thật của môi trường sau khi owner duyệt; ví dụ dưới đây chứa placeholder:

```bash
export APP_ENV=staging
export GCP_PROJECT_ID=YOUR_APPROVED_PROJECT_ID
export REGION=asia-southeast3
export SERVICE_NAME=ccdc-dashboard-staging
export GCS_BUCKET=YOUR_PRIVATE_STAGING_DATA_BUCKET
export GCS_PREFIX=ccdc/staging
export RUNTIME_SERVICE_ACCOUNT=ccdc-staging-runtime@YOUR_APPROVED_PROJECT_ID.iam.gserviceaccount.com
export BUILD_SERVICE_ACCOUNT=projects/YOUR_APPROVED_PROJECT_ID/serviceAccounts/YOUR_BUILD_ACCOUNT_EMAIL
export ARTIFACT_REGISTRY_REPOSITORY=YOUR_EXISTING_DOCKER_REPOSITORY
export BUILD_SOURCE_BUCKET=YOUR_EXISTING_BUILD_SOURCE_BUCKET
export BUILD_LOG_BUCKET=YOUR_EXISTING_BUILD_LOG_BUCKET
export ADMIN_EMAILS=approved-admin@example.com
export IAP_AUDIENCE='EXACT_EXPECTED_AUDIENCE_VERIFIED_BY_OWNER'
export DEPLOY_RECORD_DIR=/path/to/private/operational/release-records
export CLOUD_RESOURCES_APPROVED=true
export STAGING_SYNTHETIC_ONLY=true
bash scripts/deploy.sh
```

Không dùng `GCS_BUCKET` làm build source/log bucket. Không đặt credential JSON vào source, `.env` hay build args. `IAP_AUDIENCE` phải xác minh theo dịch vụ thật trước; giá trị giả trong ví dụ không thể làm IAP hoạt động. Không đặt nó bằng cách tin JWT do trình duyệt gửi đến.

Cấu hình ban đầu: 1 vCPU, 2 GiB, min 0/max 1 ở service và revision, concurrency 20, timeout 1800 giây, request-based CPU throttling. Max 1 không đảm bảo chỉ có một process trong mọi rollout và không thay CAS generation. WebSocket có timeout/reconnect, giữ tab có thể kéo dài billable request. Cần benchmark cloud bằng năm phiên trước khi chốt giới hạn tài nguyên.

Script lưu build result, commit, image digest, gcloud version, service/revision và Run IAM policy vào `DEPLOY_RECORD_DIR`. Giữ thư mục này trong kho vận hành private có retention; service JSON chứa cấu hình/email vận hành. Nếu deploy lỗi sau build, build evidence vẫn còn trong thư mục record. Các record không tự chứa dữ liệu nghiệp vụ hoặc key. Không xóa image/revision tốt gần nhất trước khi nghiệm thu.

Trên Windows: cài Git for Windows, Python và official gcloud CLI, bảo đảm lệnh chạy được từ Git Bash. Có thể export biến và chạy `bash scripts/deploy.sh` trong Git Bash. `scripts/deploy.ps1` là entry point PowerShell gọi cùng script Git Bash để giữ một implementation. Trong PowerShell đặt biến bằng `$env:APP_ENV='staging'` và các biến tương ứng rồi `./scripts/deploy.ps1`. Nếu tổ chức chỉ cho phép WSL/Cloud Shell, chạy Bash script trong môi trường đó; không có bản native PowerShell độc lập được xác minh trong phiên này.

## 4. Nghiệm thu staging qua URL IAP thật

Owner hoàn tất IAP service-agent invoker và các binding user theo CLOUD_SETUP. Admin ingest **file giả** vào bucket staging bằng CLI, không upload web. Test từ đúng URL Cloud Run có IAP, bao gồm WebSocket/static assets; không dùng local container hoặc `gcloud run services proxy` để chứng minh IAP đã bảo vệ public URL.

Ghi evidence thời gian, revision, version dữ liệu và kết quả cho từng kiểm tra:

- Đủ năm người được duyệt đăng nhập, filter/export đúng; user ngoài danh sách và cửa sổ chưa login bị chặn. Thử request giả header email không được cấp quyền. Kiểm tra Cloud Run/IAP/bucket không có broad public membership.
- Năm phiên đồng thời, mở lại sau cold start, reconnect qua timeout, đổi filter/export. Đo latency, peak RAM và lỗi; file ~100.000 dòng đã thành Parquet, không parse Excel mỗi request.
- Admin preview/publish hợp lệ; Viewer ingestion bị từ chối; file sai/oversize/có formula/định dạng giả bị chặn; active version không đổi khi lỗi. Hai preview cùng generation: chỉ một publish thắng, lần còn lại phải báo conflict.
- Browser thấy version/ngày dữ liệu mới sau refresh; rollback data độc lập vẫn giữ code revision; code rollback độc lập không sửa active pointer.
- Logging không có dòng Excel, JWT hoặc credential. Theo dõi CPU/RAM/request/WebSocket, build/storage/egress và Billing Alert. Ghi nhận quyền runtime không ghi được bucket.

Kết quả trong `docs/VALIDATION_RESULTS.md` phải phân biệt test local/mock/container với kiểm tra cloud chưa thực hiện. Chưa pass checklist này thì chưa xác nhận go-live.

## 5. Production và quy trình sửa code

Sau nghiệm thu staging và phê duyệt dữ liệu, chuyển sang project/bucket/runtime account/IAP audience production đã duyệt, `APP_ENV=production`, tên service riêng, thêm:

```bash
export DATA_CLOUD_APPROVED=true
export STAGING_ACCEPTANCE_APPROVED=true
bash scripts/deploy.sh
```

Các cờ là xác nhận của người vận hành có thẩm quyền. Không tự đặt để bỏ qua đầu vào còn thiếu. Lần đầu production chỉ mở quyền user sau khi xác minh IAP/IAM, ingestion, KPI và log. Cập nhật source sau này: feature branch → test → review/merge → commit sạch → cùng script build/deploy → smoke qua IAP. Không sửa file trực tiếp trong container. CI/CD về sau ưu tiên Workload Identity Federation/OIDC, không dùng service-account JSON key trong GitHub Secrets.

## 6. Rollback code

Rollback code chỉ đổi traffic; không ghi `metadata/active.json` và không ingest lại Excel. Xác định revision tốt từ release records, kiểm tra revision còn dùng runtime/IAP/config phù hợp và schema dữ liệu tương thích. Owner/deployer chạy:

```bash
gcloud run revisions list --service="$SERVICE_NAME" --region="$REGION" --project="$GCP_PROJECT_ID"
gcloud run services update-traffic "$SERVICE_NAME" --region="$REGION" \
  --project="$GCP_PROJECT_ID" --to-revisions=KNOWN_GOOD_REVISION=100
gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$GCP_PROJECT_ID"
```

Thay `KNOWN_GOOD_REVISION` bằng revision đã đối chiếu commit/digest, không dùng tên đoán. Test lại qua IAP, kiểm active version giữ nguyên và lưu quyết định/traffic vào record. Nếu revision đã bị xóa, deploy lại image digest đã ghi sau khi kiểm tra compatibility và giữ nguyên các cờ IAP/IAM. Không rebuild code cũ rồi giả định digest sẽ giống image cũ.

## 7. Rollback data

Rollback data giữ nguyên revision Cloud Run. Admin dùng cùng cấu hình GCS/ADC hợp lệ:

```bash
python scripts/ingest_excel.py list
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION
# Read preview; copy exact confirmation_token and expected_generation:
python scripts/ingest_excel.py rollback --version KNOWN_GOOD_VERSION \
  --confirm TOKEN --expected-generation GENERATION
```

CLI đọc/kiểm tra snapshot trước khi đổi active pointer, dùng generation precondition. Nếu generation đã đổi: dừng, đọc lại trạng thái và xin quyết định mới của Admin; không cưỡng bức ghi đè. Giữ version lỗi để đối soát. Sau rollback refresh dashboard, đối chiếu version, ngày dữ liệu, số dòng/KPI và lưu log sự cố. Xem chi tiết cập nhật tuần tại [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md).
