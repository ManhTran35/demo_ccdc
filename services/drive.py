"""Read-only Google Drive snapshots. Drive is durable; SQLite/Parquet is cache.

No Drive writes, no browser scraping, no user-controlled download URLs. Files
are validated through the same bounded Excel pipeline as local ingestion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import re
import threading
import time

from core import vn_today
from services.config import Settings, create_storage
from services.drive_cache import DriveSnapshotCache
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
NORMALIZATION_REVISION = "day-first-v3-validation"
logger = logging.getLogger(__name__)


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
            raise DriveError("Google Drive tạm thời không đáp ứng hoặc hết hạn mức. Thử làm mới dữ liệu sau.")
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
                raise DriveError("Checksum file Drive đã đổi. Đợi upload hoàn tất rồi làm mới dữ liệu.")
            return content
        except DriveError:
            raise
        except Exception:
            raise DriveError("Tải file Drive bị gián đoạn. Hãy làm mới dữ liệu để thử lại.") from None
        finally:
            response.close()


class DriveRepository(DataRepository):
    """Validate changed source pairs once, retain last good snapshot on failure."""
    def __init__(self, settings, config, client, storage=None):
        self.settings = settings or Settings.from_env()
        self.manager = DriveSnapshotCache(storage if storage is not None else create_storage(self.settings))
        self.config, self.client = config, client
        self.last_error = ""
        self.last_checked = ""
        self.last_failures = []
        self.files = []
        self.reference = None
        self._next_check = 0
        self._lock = threading.RLock()
        self._versions = {}
        self._classification = None

    def _fingerprint(self, inventory, reference):
        source = [NORMALIZATION_REVISION, inventory.stamp, reference.stamp]
        return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()

    def _download(self, file, role):
        logger.info("drive_download_started role=%s file=%r file_id=%s revision=%r", role, file.name, file.id, file.version)
        content = self.client.download(file)
        logger.info("drive_download_completed role=%s file=%r bytes=%s", role, file.name, len(content))
        return content

    @staticmethod
    def _validation_failure(exc, file, role):
        message = str(exc) if exc.report.get("filename") else f"File '{file.name}': {exc}"
        return ValidationError(message, code=exc.code, report=dict(
            exc.report, filename=file.name, file_id=file.id, source_role=role, errors=[message]))

    def _load_classification(self, reference):
        if self._classification and self._classification[0] == reference.stamp:
            return self._classification[1:]
        content = self._download(reference, "reference")
        if self.client.metadata(reference.id).stamp != reference.stamp:
            raise DriveError("File tham chiếu đang được cập nhật. Đợi upload hoàn tất rồi làm mới dữ liệu.")
        filename = reference.name if reference.name.lower().endswith(".xlsx") else reference.name + ".xlsx"
        try:
            mapping, report = parse_classification(content, self.settings, filename)
        except ValidationError as exc:
            raise self._validation_failure(exc, reference, "reference") from None
        self._classification = (reference.stamp, content, mapping, report)
        return content, mapping, report

    def _prepare(self, inventory, reference, activate):
        fingerprint = self._fingerprint(inventory, reference)
        if not activate and fingerprint in self._versions:
            return self._versions[fingerprint]
        classification, mapping, _ = self._load_classification(reference)
        content = self._download(inventory, "inventory")
        # Refuse a mixed revision if either file changed during the download.
        if self.client.metadata(inventory.id).stamp != inventory.stamp or self.client.metadata(reference.id).stamp != reference.stamp:
            raise DriveError("File Drive đang được cập nhật. Đợi upload hoàn tất rồi làm mới dữ liệu.")
        filename = inventory.name if inventory.name.lower().endswith(".xlsx") else inventory.name + ".xlsx"
        logger.info("drive_processing_started file=%r data_date=%s", inventory.name, inventory.data_date)
        try:
            result = transform_inventory(content, mapping, settings=self.settings, filename=filename)
        except ValidationError as exc:
            raise self._validation_failure(exc, inventory, "inventory") from None
        logger.info("drive_processing_completed file=%r rows=%s", inventory.name, result.report["row_count"])
        result.report["drive"] = {"fingerprint": fingerprint, "inventory_id": inventory.id,
                                  "reference_id": reference.id, "source_name": inventory.name,
                                  "reference_name": reference.name,
                                  "source_modified_at_utc": inventory.modified_time,
                                  "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
                                  "date_format": "dd/mm/yyyy", "normalization_revision": NORMALIZATION_REVISION}
        active = self.get_active_version()
        generation = active["_generation"] if active else 0
        self.manager.materialize(fingerprint, inventory.name, inventory.data_date.isoformat(), content, classification, result, mapping)
        if activate:
            self.manager.activate(fingerprint, generation)
            logger.info("drive_cache_activated file=%r data_date=%s version=%s rows=%s", inventory.name, inventory.data_date, fingerprint, result.report["row_count"])
        version = {"id": fingerprint, "cutoff": inventory.data_date.isoformat()}
        self._versions[fingerprint] = version
        return version

    def synchronize(self, force=False):
        with self._lock:
            if not force and time.monotonic() < self._next_check:
                return
            self.last_checked = datetime.now(timezone.utc).isoformat()
            self.last_error = ""
            self.last_failures = []
            logger.info("drive_sync_started force=%s has_active=%s", force, bool(self.get_active_version()))
            try:
                if force and hasattr(self.client, "clear_cache"):
                    self.client.clear_cache()
                files = self.client.list_inventory(self.config)
                eligible = [f for f in files if f.data_date <= vn_today()]
                if not eligible:
                    raise DriveError("Chưa có bản chụp hợp lệ trong Data. Dùng tên Ton_CCDC_ddmmyyyy.xlsx, ví dụ Ton_CCDC_15102026.xlsx; không dùng ngày tương lai.")
                eligible.sort(key=lambda f: (f.data_date, f.modified_time, f.version, f.id), reverse=True)
                logger.info("drive_inventory_listed total=%s eligible=%s latest=%r", len(files), len(eligible), eligible[0].name)
                reference = self.client.metadata(self.config.reference_file_id)
                self.files, self.reference = eligible, reference
                active = self.get_active_version()
                previous = self.manager.read_validation(active["active_version"]).get("drive", {}) if active else {}
                # With an accepted cache, preserve that exact snapshot on error.
                # After reboot, recover by revalidating durable Drive history.
                candidates = eligible[:1] if active else eligible
                for candidate in candidates:
                    if self._fingerprint(candidate, reference) == previous.get("fingerprint"):
                        logger.info("drive_cache_unchanged file=%r", candidate.name)
                        break
                    try:
                        self._prepare(candidate, reference, activate=True)
                        break
                    except ValidationError as exc:
                        self.last_failures.append(exc.report)
                        logger.warning("drive_candidate_rejected file=%r role=%s code=%s sheet=%r cell=%s",
                                       exc.report.get("filename"), exc.report.get("source_role"), exc.code,
                                       exc.report.get("sheet", ""), exc.report.get("cell", ""))
                        if active or exc.report.get("source_role") == "reference":
                            break  # All inventory candidates share this reference.
                if self.last_failures:
                    messages = [report["errors"][0] for report in self.last_failures[:3]]
                    self.last_error = "\n\n".join(messages)
                    if len(self.last_failures) > 3:
                        self.last_error += f"\n\nCòn {len(self.last_failures) - 3} file không hợp lệ; xem logs."
                self.last_checked = getattr(self.client, "last_listed_at", "") or datetime.now(timezone.utc).isoformat()
            except (DriveError, ValidationError) as exc:
                self.last_error = str(exc)
            except PreconditionFailed:
                self.last_error = "Bản chụp vừa được phiên đồng bộ khác cập nhật. Hãy làm mới dữ liệu."
            except Exception as exc:
                logger.error("drive_sync_failed error_type=%s", type(exc).__name__)
                self.last_error = "Không đồng bộ được Drive. Kiểm tra cấu hình, kết nối và logs ứng dụng."
            finally:
                self._next_check = time.monotonic() + self.config.refresh_seconds
                logger.log(logging.WARNING if self.last_error else logging.INFO,
                           "drive_sync_completed has_active=%s rejected=%s has_error=%s",
                           bool(self.get_active_version()), len(self.last_failures), bool(self.last_error))

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
            return self._prepare(selected, self.reference, activate=False)
