# Thiết lập Google Cloud — V1-A

Đây là hướng dẫn chuẩn bị hạ tầng, không phải biên bản đã triển khai. Chưa có Project ID, Billing, danh sách năm người dùng, phê duyệt dữ liệu hoặc kết quả kiểm thử IAP trên cloud trong phiên bàn giao này. Không có URL production để công bố.

Kiến trúc: Streamlit hiện có → **IAP trực tiếp trên Cloud Run** → runtime service account chỉ đọc → bucket private. Admin dùng CLI ingestion bằng danh tính ADC riêng; web staging/production chỉ xem, lọc và export. Không cần load balancer, SQL, VM hay trang upload web ở V1-A.

## 1. Quyết định và phê duyệt trước khi tạo tài nguyên

Owner/CNTT xác nhận tài khoản quản trị, project, billing, region, nơi lưu source và dữ liệu được phép sử dụng. Dùng `asia-southeast3` nếu Cloud Run, Cloud Build, Artifact Registry và Storage hỗ trợ trong project/chính sách tổ chức; giữ cùng region khi khả thi. Staging dùng dữ liệu giả và bucket riêng; không chép bộ dữ liệu cũ lên cloud để thử.

Repository phải private hoặc nằm trong hệ thống Git được đơn vị duyệt. Baseline `c591728` đã chứa Excel thật và HTML/ZIP nhúng dữ liệu. Bỏ file khỏi tree mới **không xóa lịch sử Git**. Owner cần kiểm tra visibility, người đã có quyền truy cập và quyết định xử lý lịch sử theo quy trình nội bộ; tác vụ này không tự đổi visibility, rewrite history hoặc force-push. Đừng đưa cả `.git` vào Cloud Build. Script deploy chỉ xuất một tập file nguồn cụ thể từ commit đã review.

Production chỉ được ingest dữ liệu thật sau phê duyệt lưu trữ/xử lý trên Google Cloud và nghiệm thu staging. Các biến `CLOUD_RESOURCES_APPROVED`, `DATA_CLOUD_APPROVED` trong script ghi nhận quyết định đó, không thay thế văn bản phê duyệt.

## 2. Project, Billing và API

Owner tạo/chọn project riêng tại [Google Cloud Console](https://console.cloud.google.com/), gắn Billing Account được duyệt và đặt Budget Alert theo ngân sách nội bộ. Alert không tự ngắt chi phí. Kiểm tra quyền và API trước khi chạy lệnh tạo tài nguyên; không cấp Owner/Editor cho các service account chỉ để bỏ qua lỗi quyền.

API cần dùng: `run.googleapis.com`, `cloudbuild.googleapis.com`, `artifactregistry.googleapis.com`, `storage.googleapis.com`, `iam.googleapis.com`, `iap.googleapis.com`, `cloudresourcemanager.googleapis.com`, `serviceusage.googleapis.com`, `logging.googleapis.com`. Bật thêm API khi Google Cloud yêu cầu cho cấu hình được duyệt; ứng dụng không cần Secret Manager ở V1-A vì không sử dụng key/password trong source.

Các lệnh dưới đây là mẫu **chỉ dành cho owner sau phê duyệt**, không tự động chạy khi khởi động app:

```bash
gcloud auth login
gcloud projects describe "$GCP_PROJECT_ID"
gcloud billing projects describe "$GCP_PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com iam.googleapis.com \
  iap.googleapis.com cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
  logging.googleapis.com --project="$GCP_PROJECT_ID"
```

Kiểm tra account/project trước mỗi thao tác. Đăng nhập `gcloud` dùng cho deploy và ADC dùng cho ingestion là hai cấu hình riêng; một cấu hình thành công không chứng minh cấu hình còn lại có quyền.

## 3. Bucket, repository image và danh tính

Chuẩn bị các tài nguyên sau, với tên thật do owner chọn; không giữ nguyên placeholder:

| Tài nguyên | Thiết lập |
|---|---|
| Data bucket staging | Standard, private, dữ liệu giả; region phù hợp |
| Data bucket production | Standard, private; chỉ dùng khi có phê duyệt |
| Build source bucket | Riêng với data, private; chứa source archive đã allowlist |
| Build log bucket | Riêng với data, private; lưu build log theo retention nội bộ |
| Artifact Registry | Docker repository đã tạo sẵn cùng region |
| Runtime service account | Riêng cho từng môi trường; **chỉ `roles/storage.objectViewer` trên data bucket tương ứng** |
| Build service account | Riêng runtime; đọc source, ghi build logs, ghi image vào repository cụ thể |
| Deployer | Được tạo build/deploy, actAs build/runtime account; không dùng runtime account làm người deploy |
| Ingestion Admin | Google identity thật trong `ADMIN_EMAILS` và có IAM ghi dữ liệu riêng; không dùng runtime account |

Mọi bucket bật **Public Access Prevention = enforced** và **Uniform bucket-level access**. Không cấp `allUsers` hoặc `allAuthenticatedUsers`. Dashboard viewers không có quyền đọc bucket trực tiếp. Nếu dùng chung project, vẫn giữ bucket staging/production và IAM riêng; prefix riêng giúp tổ chức dữ liệu nhưng không tự tạo ranh giới quyền.

Ví dụ owner tạo bucket/data account (chỉ sau phê duyệt):

```bash
gcloud storage buckets create "gs://$GCS_BUCKET" --project="$GCP_PROJECT_ID" \
  --location=asia-southeast3 --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create ccdc-runtime --project="$GCP_PROJECT_ID"
gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET" \
  --member="serviceAccount:$RUNTIME_SERVICE_ACCOUNT" --role=roles/storage.objectViewer
```

Lệnh `create` không dùng cho tài nguyên đã tồn tại. Owner tạo build buckets/repository/service account theo cùng nguyên tắc. Script deploy sẽ kiểm tra tài nguyên tồn tại thay vì yêu cầu Cloud Build tự tạo bucket mặc định.

Gợi ý phân quyền để owner rà soát theo chính sách tổ chức:

- Build account: Artifact Registry Writer trên đúng repository; Storage Object Viewer trên source bucket; quyền tạo log object trên log bucket. Không có quyền data bucket.
- Deployer: quyền tạo/đọc build và deploy Cloud Run đúng phạm vi; `iam.serviceAccounts.actAs` với build/runtime account; ghi source archive vào source bucket. Sửa IAM dịch vụ cần quyền bổ sung do owner cấp. Không mặc định cấp quyền đọc dữ liệu production.
- Ingestion Admin: đọc/list và tạo object cho snapshot; thay `metadata/active.json` cần quyền overwrite object. Có thể dùng custom role + IAM Conditions để giới hạn quyền xóa/ghi đè vào active pointer và quyền tạo vào các prefix snapshot; phải kiểm thử chính sách thật. `Storage Object User` trên toàn bucket cho phép nhiều hơn CLI cần, nên chỉ dùng nếu owner chấp nhận phạm vi. Admin allowlist trong ứng dụng bổ sung kiểm tra IAM, không thay thế IAM.

Bật Object Versioning nếu owner chọn để phục hồi generation cũ của active pointer; đánh giá dung lượng và chi phí trước. **V1 không cấu hình lifecycle tự động xóa dữ liệu.** Chỉ bổ sung hạn lưu staging/logs/noncurrent generations ở thay đổi riêng sau khi thống nhất yêu cầu lưu trữ và xác định mọi tham chiếu còn cần giữ. Không xóa `raw/`, `versions/` hoặc active version. Các bản snapshot bất biến không bị xóa khi rollback.

## 4. ADC cho Admin ingestion

Trên máy được đơn vị duyệt hoặc Cloud Shell, dùng môi trường Python đã cài dependencies của repository. Không tạo/download JSON service-account key. Kiểm tra binding ADC sẵn có trước; không in file credential hoặc giá trị token. Nếu ADC chưa có hoặc thiếu scope/email verified, Admin chạy:

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,openid
gcloud auth application-default set-quota-project "$GCP_PROJECT_ID"
```

CLI xác minh email được Google trả về từ userinfo bằng ADC, yêu cầu `email_verified=true`, rồi so với `ADMIN_EMAILS`. Không có cờ tự khai actor, không tin email truyền bằng env hoặc tên file. Thay `ADMIN_EMAILS` cần review cấu hình; thêm vào allowlist không tự cấp quyền GCS. Một service account ADC không cung cấp userinfo phù hợp sẽ bị chặn; dùng tài khoản Admin người thật cho V1-A.

## 5. IAP trực tiếp trên Cloud Run

SDK Google Cloud **589.0.0** đã được lấy từ `dl.google.com` và kiểm tra SHA-256 từng component theo manifest chính thức ngày 15/10/2026. Help của phiên bản này xác nhận `gcloud run deploy --iap`, `--no-allow-unauthenticated`, `--invoker-iam-check`, `--cpu-throttling`, và IAP binding `--resource-type=cloud-run --region --service`. Không dùng tài liệu/repo mirror không chính thức để quyết định cờ bảo mật.

Owner hoàn tất cấu hình OAuth/organization cho IAP theo [tài liệu Google](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run). Xác nhận năm người dùng dùng Google Workspace/Gmail hoặc loại identity được IAP hỗ trợ trong cấu hình thật; không giả định tài khoản Microsoft 365 đăng nhập được trực tiếp. Với identity ngoài tổ chức cần kiểm tra OAuth consent và chính sách external-user.

**IAP_AUDIENCE phải là expected audience chính xác cho JWT IAP của dịch vụ này**, lấy từ cấu hình/tài liệu chính thức đã được owner kiểm chứng. Không suy ra từ tên service/URL, không thay bằng OAuth client ID tùy ý, và không lấy `aud` từ JWT chưa xác minh làm giá trị tin cậy. App staging/production từ chối khởi động nếu thiếu audience; không tắt kiểm tra để thử. Hai domain tài liệu Google đã bị egress proxy trả 403 trong máy thực thi; vì vậy audience cụ thể chưa thể xác minh cho một dịch vụ chưa được tạo. Đây là đầu vào vận hành cần hoàn tất trước deploy.

Script bật IAP ngay trong lệnh deploy và không có giai đoạn public. Owner đảm bảo IAP service agent của project được phép invoke đúng Cloud Run service và giữ invoker IAM check bật. Nếu cần binding, danh tính Google-managed có dạng `service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com`; xác minh project number và service identity trước khi cấp `roles/run.invoker` trên **dịch vụ đó**, không cấp invoker cho người xem hoặc cả Internet để sửa lỗi.

Sau deploy staging, owner cấp từng người dùng/nhóm đã duyệt quyền **IAP-secured Web App User** trên đúng IAP resource. Ví dụ cú pháp đã đối chiếu help SDK:

```bash
gcloud iap web add-iam-policy-binding --project="$GCP_PROJECT_ID" \
  --resource-type=cloud-run --region=asia-southeast3 --service=ccdc-dashboard-staging \
  --member="user:approved-viewer@example.com" --role=roles/iap.httpsResourceAccessor
```

Không dùng placeholder làm danh sách người dùng thực tế. Xác minh không còn `allUsers`/`allAuthenticatedUsers` ở Cloud Run, IAP hoặc bucket; app vẫn tự kiểm chữ ký/issuer/audience/expiry của `X-Goog-IAP-JWT-Assertion`. Header email đơn lẻ không được tin cậy. Runtime chỉ đọc nên cả Admin đăng nhập web cũng không publish/rollback từ web ở V1-A.

## 6. Cấu hình app và network

`.env.example` chỉ chứa placeholder, không tự load `.env`. Export đúng biến qua shell/container/Cloud Run env. Các biến: `APP_ENV=local|staging|production`, `STORAGE_BACKEND=local|gcs`, `LOCAL_DATA_DIR`, `GCP_PROJECT_ID`, `GCS_BUCKET`, `GCS_PREFIX`, `MAX_UPLOAD_MB`, `ADMIN_EMAILS`, `IAP_AUDIENCE`, `LOG_LEVEL`. Staging/production bắt buộc `gcs` và IAP audience; không fallback về Excel local khi GCS lỗi. Không tự chèn `GOOGLE_APPLICATION_CREDENTIALS` vào Cloud Run: attached runtime account cung cấp ADC.

Khi có egress proxy/allowlist, bổ sung đúng điểm đích cần dùng vào **danh sách hiện tại**, không thay cả danh sách chưa biết:

| Luồng | Điểm đích chính |
|---|---|
| Cài dependencies | `pypi.org`, `files.pythonhosted.org`; Docker base image qua Docker Hub/registry tổ chức |
| Cài/kiểm tra SDK | `dl.google.com`; tài liệu `docs.cloud.google.com`, `cloud.google.com` |
| Google login/ADC | `accounts.google.com`, `oauth2.googleapis.com`, `openidconnect.googleapis.com` |
| GCS/API | `storage.googleapis.com`, `*.googleapis.com` theo API thực tế; metadata server trong Cloud Run |
| Kiểm JWT IAP | `www.gstatic.com` (`/iap/verify/public_key`) |
| Build/deploy | `cloudbuild.googleapis.com`, `run.googleapis.com`, `artifactregistry.googleapis.com`, `<region>-docker.pkg.dev` và các endpoint SDK ghi nhận |

Đây là inventory phục vụ chẩn đoán, không cam kết đủ cho mọi proxy/tổ chức. Giữ TLS, chữ ký package và checksum bật. Không log JWT, raw Excel, dòng dữ liệu, file credential hoặc dump env để tìm lỗi mạng.

## 7. Điểm dừng trước production

Hoàn tất [deploy staging và checklist](DEPLOY_AND_ROLLBACK.md) bằng dữ liệu giả; kiểm tra năm user, user bị từ chối, giả header, WebSocket/static assets, cache đổi version, publish conflict, rollback, RAM và chi phí. Owner ký nghiệm thu và phê duyệt dữ liệu trước khi dùng production. Các test local/mock/container không chứng minh IAP/IAM hoặc tài khoản thật đã cấu hình đúng.
