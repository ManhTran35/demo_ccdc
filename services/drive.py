"""Read-only Google Drive snapshots. Drive is durable; SQLite/Parquet is cache.

No Drive writes, no browser scraping, no user-controlled download URLs. Files
are validated through the same bounded Excel pipeline as local ingestion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
import threading
import time

from core import vn_today
from services.repository import DataRepository
from services.storage import PreconditionFailed
from services.transform import parse_classification, transform_inventory
from services.validation import ValidationError

FOLDER_ID = "1iqFTwprrjOM587-pbF4gsivh-2lnDI2D"
REFERENCE_ID = "1VSXLhu2Iovyyq11r0HlGOFfjCG3txfJ4"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SHEET = "application/vnd.google-apps.spreadsheet"
FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,name,mimeType,modifiedTime,version,md5Checksum,size"
NORMALIZATION_REVISION = "day-first-v2"


class DriveError(ValueError):
    """Actionable errors containing neither credentials nor response bodies."""


def _file_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value):
        raise DriveError("Google Drive ID không hợp lệ; dùng ID trong liên kết, không dán toàn bộ URL.")
    return value


def snapshot_date(name):
    """Ton_CCDC_15102026[.xlsx] means 15 October, with no locale inference."""
    match = re.fullmatch(r"Ton_CCDC_(\d{2})(\d{2})(\d{4})(?:\.xlsx)?", name, re.IGNORECASE)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise DriveError("Ngày trong tên file Ton_CCDC không hợp lệ. Ví dụ đúng: Ton_CCDC_15102026.xlsx.") from None


@dataclass(frozen=True)
class DriveConfig:
    folder_id: str = FOLDER_ID
    reference_file_id: str = REFERENCE_ID
    data_subfolder: str = "Data"
    recursive: bool = True
    refresh_seconds: int = 1800

    def __post_init__(self):
        _file_id(self.folder_id)
        _file_id(self.reference_file_id)
        if not isinstance(self.data_subfolder, str) or not self.data_subfolder.strip():
            raise DriveError("Tên thư mục Data không được trống.")
        if not isinstance(self.recursive, bool):
            raise DriveError("google_drive.recursive phải là true hoặc false.")
        if isinstance(self.refresh_seconds, bool) or not isinstance(self.refresh_seconds, int) or not 30 <= self.refresh_seconds <= 86400:
            raise DriveError("google_drive.refresh_seconds phải từ 30 đến 86400 giây.")

    @classmethod
    def from_mapping(cls, values):
        allowed = {"folder_id", "reference_file_id", "data_subfolder", "recursive", "refresh_seconds"}
        if set(values) - allowed:
            raise DriveError("Cấu hình google_drive có trường không được hỗ trợ. Xem docs/GOOGLE_DRIVE.md.")
        return cls(**dict(values))


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str = ""
    version: str = ""
    checksum: str = ""
    size: int = 0

    @classmethod
    def from_json(cls, value):
        try:
            file = cls(_file_id(value["id"]), str(value["name"]), str(value["mimeType"]),
                       str(value.get("modifiedTime", "")), str(value.get("version", "")),
                       str(value.get("md5Checksum", "")), int(value.get("size", 0)))
            if file.size < 0:
                raise ValueError()
            return file
        except (KeyError, TypeError, ValueError):
            raise DriveError("Google Drive trả metadata file không hợp lệ.") from None

    @property
    def data_date(self):
        return snapshot_date(self.name)

    @property
    def stamp(self):
        if not self.version and not self.modified_time:
            raise DriveError("Thiếu thông tin phiên bản Google Drive; chưa thể xác minh file.")
        return (self.id, self.name, self.mime_type, self.version, self.modified_time, self.checksum, self.size)


class DriveClient:
    BASE = "https://www.googleapis.com/drive/v3"

    def __init__(self, session, max_bytes=25 * 1024 * 1024):
        self.session = session
        self.max_bytes = max_bytes

    @classmethod
    def from_service_account(cls, info=None, max_bytes=25 * 1024 * 1024):
        try:
            from google.auth.transport.requests import AuthorizedSession
            if info:
                from google.oauth2.service_account import Credentials
                credentials = Credentials.from_service_account_info(dict(info), scopes=[DRIVE_SCOPE])
            else:
                import google.auth
                credentials, _ = google.auth.default(scopes=[DRIVE_SCOPE])
            return cls(AuthorizedSession(credentials), max_bytes)
        except Exception:
            raise DriveError("Chưa có quyền đọc Drive. Thêm gcp_service_account vào Streamlit Secrets và chia sẻ thư mục Data cùng file tham chiếu cho email service account.") from None

    def _get(self, path, params=None, stream=False):
        try:
            response = self.session.get(self.BASE + path, params=params or {}, stream=stream,
                                        timeout=(10, 90), allow_redirects=False)
        except Exception:
            raise DriveError("Không kết nối được Google Drive. Kiểm tra mạng và thử đồng bộ lại.") from None
        if response.status_code != 200:
            status = response.status_code
            response.close()
            if path.endswith("/export") and status == 403:
                raise DriveError("Không export được Google Sheets. Kiểm tra quyền đọc và giới hạn export của Google; có thể thay bằng file XLSX chỉ chứa giá trị.")
            if status in {401, 403, 404}:
                raise DriveError("Không có quyền đọc Drive hoặc file không tồn tại. Kiểm tra Drive API, ID và quyền Viewer của service account trên cả Data lẫn file tham chiếu.")
            raise DriveError("Google Drive tạm thời không đáp ứng hoặc hết hạn mức. Giữ bản dữ liệu đang xem và thử lại sau.")
        return response

    def _json(self, path, params):
        response = self._get(path, params)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise DriveError("Không đọc được metadata Google Drive.") from None
        finally:
            response.close()

    def metadata(self, file_id):
        return DriveFile.from_json(self._json("/files/" + _file_id(file_id),
                                             {"fields": FIELDS, "supportsAllDrives": "true"}))

    def _children(self, folder_id):
        page = None
        result = []
        seen = set()
        while True:
            params = {"q": f"'{_file_id(folder_id)}' in parents and trashed = false",
                      "fields": f"nextPageToken,incompleteSearch,files({FIELDS})", "pageSize": 100,
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if page:
                params["pageToken"] = page
            data = self._json("/files", params)
            if data.get("incompleteSearch"):
                raise DriveError("Drive chưa trả đủ danh sách file. Không chọn bản chụp từ danh sách thiếu.")
            try:
                result.extend(DriveFile.from_json(item) for item in data["files"])
            except (KeyError, TypeError):
                raise DriveError("Danh sách file Drive không hợp lệ.") from None
            if len(result) > 1000:
                raise DriveError("Thư mục Drive vượt giới hạn 1.000 mục; hãy tách thư mục Data.")
            page = data.get("nextPageToken")
            if not page:
                return result
            if page in seen:
                raise DriveError("Google Drive trả trang danh sách lặp; chưa đồng bộ.")
            seen.add(page)

    def list_inventory(self, config):
        root = self.metadata(config.folder_id)
        if root.mime_type != FOLDER:
            raise DriveError("folder_id phải trỏ tới thư mục Google Drive.")
        children = self._children(root.id)
        # The supplied link may point directly to Data or to its parent.
        if root.name.casefold() != config.data_subfolder.casefold():
            folders = [f for f in children if f.mime_type == FOLDER and f.name.casefold() == config.data_subfolder.casefold()]
            if len(folders) > 1:
                raise DriveError("Có nhiều thư mục cùng tên Data; cấu hình folder_id trỏ trực tiếp tới thư mục đúng.")
            if folders:
                root = folders[0]
                children = self._children(root.id)
        queue = [(root.id, children)]
        visited = set()
        inventory = {}
        while queue:
            folder_id, items = queue.pop(0)
            if folder_id in visited:
                continue
            visited.add(folder_id)
            if len(visited) > 100:
                raise DriveError("Cây Data vượt 100 thư mục; hãy thu hẹp folder_id.")
            if items is None:
                items = self._children(folder_id)
            for item in items:
                if config.recursive and item.mime_type == FOLDER and item.id not in visited:
                    queue.append((item.id, None))
                elif item.id != config.reference_file_id and item.mime_type in {XLSX, SHEET}:
                    try:
                        data_date = item.data_date
                    except DriveError:
                        continue  # An invalid calendar date is never a candidate.
                    if data_date:
                        inventory[item.id] = item
                if len(inventory) > 500:
                    raise DriveError("Data vượt 500 bản chụp; hãy thu hẹp thư mục dữ liệu.")
        return list(inventory.values())

    def download(self, file):
        if file.mime_type not in {XLSX, SHEET}:
            raise DriveError("Chỉ đọc Excel .xlsx hoặc Google Sheets.")
        if file.size > self.max_bytes:
            raise DriveError("File Drive vượt giới hạn dung lượng đã cấu hình.")
        path = "/files/" + _file_id(file.id)
        params = {"mimeType": XLSX} if file.mime_type == SHEET else {"alt": "media", "supportsAllDrives": "true"}
        response = self._get(path + ("/export" if file.mime_type == SHEET else ""), params, stream=True)
        try:
            size = 0
            chunks = []
            for chunk in response.iter_content(128 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise DriveError("File Drive vượt giới hạn dung lượng trong lúc tải.")
                chunks.append(chunk)
            content = b"".join(chunks)
            if file.checksum and hashlib.md5(content).hexdigest() != file.checksum:
                raise DriveError("Checksum file Drive đã đổi. Giữ bản hiện hành và đồng bộ lại.")
            return content
        except DriveError:
            raise
        except Exception:
            raise DriveError("Tải file Drive bị gián đoạn. Bản hiện hành được giữ nguyên.") from None
        finally:
            response.close()


class DriveRepository(DataRepository):
    """Validate changed source pairs once, retain last good snapshot on failure."""
    def __init__(self, settings, config, client, storage=None):
        super().__init__(settings, storage)
        self.config, self.client = config, client
        self.last_error = ""
        self.last_checked = ""
        self.files = []
        self.reference = None
        self._next_check = 0
        self._lock = threading.RLock()
        self._versions = {}

    def _fingerprint(self, inventory, reference):
        source = [NORMALIZATION_REVISION, inventory.stamp, reference.stamp]
        return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()

    def _prepare(self, inventory, reference, publish):
        fingerprint = self._fingerprint(inventory, reference)
        if not publish and fingerprint in self._versions:
            return self._versions[fingerprint]
        content = self.client.download(inventory)
        classification = self.client.download(reference)
        # Refuse a mixed revision if either file changed during the download.
        if self.client.metadata(inventory.id).stamp != inventory.stamp or self.client.metadata(reference.id).stamp != reference.stamp:
            raise DriveError("File Drive đang được cập nhật. Giữ bản hiện hành và đồng bộ lại sau khi upload xong.")
        mapping, class_report = parse_classification(classification, self.settings)
        filename = inventory.name if inventory.name.lower().endswith(".xlsx") else inventory.name + ".xlsx"
        result = transform_inventory(content, mapping, settings=self.settings, filename=filename)
        actor = "google-drive-sync (read-only)"
        stage_class = self.manager.stage_upload(classification, "classification.xlsx", actor)
        self.manager.record_stage_validation(stage_class["id"], class_report)
        result.report["classification_stage_id"] = stage_class["id"]
        result.report["drive"] = {"fingerprint": fingerprint, "inventory_id": inventory.id,
                                  "reference_id": reference.id, "source_name": inventory.name,
                                  "source_modified_at_utc": inventory.modified_time,
                                  "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
                                  "date_format": "dd/mm/yyyy", "normalization_revision": NORMALIZATION_REVISION}
        stage = self.manager.stage_upload(content, filename, actor)
        self.manager.record_stage_validation(stage["id"], result.report)
        active = self.get_active_version()
        generation = active["_generation"] if active else 0
        preview = self.manager.prepare_version(stage, result.parquet_bytes, mapping, result.report, result.metrics,
                                               inventory.data_date.isoformat(), actor, generation, note="Nguồn Google Drive · bản chụp toàn bộ")
        if publish:
            self.manager.commit(preview["id"], preview["confirmation_token"], actor, generation)
        version = {"id": preview["id"], "cutoff": inventory.data_date.isoformat()}
        self._versions[fingerprint] = version
        return version

    def synchronize(self, force=False):
        with self._lock:
            if not force and time.monotonic() < self._next_check:
                return
            self._next_check = time.monotonic() + self.config.refresh_seconds
            try:
                if force and hasattr(self.client, "clear_cache"):
                    self.client.clear_cache()
                files = self.client.list_inventory(self.config)
                eligible = [f for f in files if f.data_date <= vn_today()]
                if not eligible:
                    raise DriveError("Chưa có bản chụp hợp lệ trong Data. Dùng tên Ton_CCDC_ddmmyyyy.xlsx, ví dụ Ton_CCDC_15102026.xlsx; không dùng ngày tương lai.")
                latest = max(eligible, key=lambda f: (f.data_date, f.modified_time, f.version, f.id))
                reference = self.client.metadata(self.config.reference_file_id)
                self.files, self.reference = eligible, reference
                fingerprint = self._fingerprint(latest, reference)
                active = self.get_active_version()
                previous = self.manager.read_validation(active["active_version"]).get("drive", {}) if active else {}
                if fingerprint != previous.get("fingerprint"):
                    self._prepare(latest, reference, publish=True)
                self.last_error = ""
                self.last_checked = getattr(self.client, "last_listed_at", "") or datetime.now(timezone.utc).isoformat()
                # A successful poll must not run just before cache_data expires
                # and then defer a fresh index for another full TTL.
                self._next_check = time.monotonic() + self.config.refresh_seconds
            except (DriveError, ValidationError) as exc:
                self.last_error = str(exc)
                if not self.get_active_version():
                    raise DriveError(self.last_error) from None
            except PreconditionFailed:
                self.last_error = "Bản chụp vừa được phiên đồng bộ khác cập nhật. Đang giữ bản đã xác minh; thử lại sau."
                if not self.get_active_version():
                    raise DriveError(self.last_error) from None
            except Exception:
                self.last_error = "Không đồng bộ được Drive. Bản dữ liệu đã xác minh được giữ nguyên; kiểm tra cấu hình và kết nối."
                if not self.get_active_version():
                    raise DriveError(self.last_error) from None

    def select_version(self, cutoff, active=None):
        with self._lock:
            active = active if active is not None else self.get_active_version()
            if not active:
                return None
            if cutoff >= active["data_date"]:
                return {"id": active["active_version"], "cutoff": active["data_date"]}
            candidates = [f for f in self.files if f.data_date.isoformat() <= cutoff]
            if not candidates:
                return None
            selected = max(candidates, key=lambda f: (f.data_date, f.modified_time, f.version, f.id))
            # Browsing an older file never changes the shared active pointer.
            return self._prepare(selected, self.reference, publish=False)
