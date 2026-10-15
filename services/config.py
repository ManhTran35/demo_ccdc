"""Explicit local/cloud configuration; cloud has no fallback to laptop data."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    storage_backend: str = "local"
    project_id: str = ""
    bucket: str = ""
    prefix: str = ""
    local_data_dir: Path = ROOT / "data" / "runtime-v2"
    max_upload_mb: int = 25
    max_uncompressed_mb: int = 512
    max_rows: int = 250000
    max_columns: int = 200
    admin_emails: tuple[str, ...] = ()
    iap_audience: str = ""
    log_level: str = "INFO"
    warning_limits: dict = field(default_factory=dict)

    @property
    def cloud_mode(self):
        return self.storage_backend == "gcs"

    @property
    def community_demo(self):
        return self.app_env == "community-demo"

    @property
    def read_only(self):
        return self.cloud_mode or self.community_demo

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        app_env = env.get("APP_ENV", "production" if env.get("K_SERVICE") else "local").strip().lower()
        backend = env.get("STORAGE_BACKEND", "local" if app_env in {"local", "community-demo"} else "gcs").strip().lower()
        if app_env not in {"local", "community-demo", "staging", "production"} or backend not in {"local", "gcs"}:
            raise ConfigurationError("APP_ENV hoặc STORAGE_BACKEND không hợp lệ.")
        if app_env == "community-demo" and (backend != "local" or env.get("K_SERVICE")):
            raise ConfigurationError("Community demo chỉ dùng dữ liệu giả riêng, không dùng Cloud Run/GCS.")
        if (app_env in {"staging", "production"} or env.get("K_SERVICE")) and backend != "gcs":
            raise ConfigurationError("Môi trường cloud bắt buộc dùng GCS, không fallback về file local.")
        if env.get("K_SERVICE") and app_env == "local":
            raise ConfigurationError("Không được bật chế độ local mô phỏng trên Cloud Run.")
        bucket = env.get("GCS_BUCKET", "").strip()
        project = env.get("GCP_PROJECT_ID", "").strip()
        prefix = env.get("GCS_PREFIX", "").strip("/")
        if prefix and (not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix)):
            raise ConfigurationError("GCS_PREFIX phải là đường dẫn tương đối, không chứa '..' hoặc ký tự đặc biệt.")
        if backend == "gcs" and (not project or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket)):
            raise ConfigurationError("Cần GCP_PROJECT_ID và GCS_BUCKET hợp lệ để dùng GCS.")
        audience = env.get("IAP_AUDIENCE", "").strip()
        if app_env in {"staging", "production"} and not audience:
            raise ConfigurationError("Môi trường online cần IAP_AUDIENCE được xác minh theo cấu hình IAP thực tế.")
        emails = tuple(sorted({e.strip().casefold() for e in env.get("ADMIN_EMAILS", "").split(",") if e.strip()}))
        if any(not re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", e) for e in emails):
            raise ConfigurationError("ADMIN_EMAILS chứa email không hợp lệ.")
        def positive(name, default):
            try:
                value = int(env.get(name, default))
                if value <= 0:
                    raise ValueError()
                return value
            except ValueError:
                raise ConfigurationError(f"{name} phải là số nguyên dương.") from None
        try:
            limits = json.loads(env.get("VALIDATION_WARNING_LIMITS", "{}"))
            if not isinstance(limits, dict) or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1 for v in limits.values()):
                raise ValueError()
        except (ValueError, TypeError):
            raise ConfigurationError("VALIDATION_WARNING_LIMITS phải là JSON object chứa tỷ lệ từ 0 đến 1.") from None
        level = env.get("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL không hợp lệ.")
        # A hosted demo must never reuse the local store, which may hold real uploads.
        data_dir = (Path(tempfile.gettempdir()) / f"ccdc-community-demo-{os.getpid()}"
                    if app_env == "community-demo" else Path(env.get("LOCAL_DATA_DIR", str(ROOT / "data" / "runtime-v2"))).expanduser())
        return cls(app_env=app_env, storage_backend=backend, project_id=project, bucket=bucket,
                   prefix=prefix, local_data_dir=data_dir,
                   max_upload_mb=positive("MAX_UPLOAD_MB", 25), max_uncompressed_mb=positive("MAX_UNCOMPRESSED_MB", 512),
                   max_rows=positive("MAX_ROWS", 250000), max_columns=positive("MAX_COLUMNS", 200),
                   admin_emails=emails, iap_audience=audience, log_level=level, warning_limits=limits)


def create_storage(settings=None):
    from services.storage import GCSStorageAdapter, LocalStorageAdapter
    settings = settings or Settings.from_env()
    if settings.cloud_mode:
        from google.cloud import storage
        return GCSStorageAdapter(settings.bucket, settings.prefix, client=storage.Client(project=settings.project_id))
    return LocalStorageAdapter(settings.local_data_dir)
