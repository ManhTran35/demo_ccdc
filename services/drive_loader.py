"""Service Account Drive loader; memory-only caches expire after 30 minutes.

Excel bytes feed the existing validation -> Pandas -> Parquet pipeline.
Credentials and clients are never serialized into cache_data.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from services.drive import DriveClient, DriveConfig, DriveError, DriveRepository

CACHE_TTL_SECONDS = 30 * 60


@st.cache_data(ttl=CACHE_TTL_SECONDS, max_entries=8, show_spinner=False)
def _cached_inventory(scope, config, _client):
    return _client.list_inventory(config), datetime.now(timezone.utc).isoformat()


@st.cache_data(ttl=CACHE_TTL_SECONDS, max_entries=2, show_spinner=False)
def _cached_excel(scope, file, _client):
    # Cache bytes, avoiding a large DataFrame copy for every viewer.
    return _client.download(file)


class CachedDriveClient:
    def __init__(self, client, scope):
        self._client, self.scope = client, scope
        self._configs, self._files = set(), set()
        self.last_listed_at = ""

    def list_inventory(self, config):
        self._configs.add(config)
        files, self.last_listed_at = _cached_inventory(self.scope, config, self._client)
        return files

    def metadata(self, file_id):
        # Revision checks after download always query fresh API metadata.
        return self._client.metadata(file_id)

    def download(self, file):
        self._files.add(file)
        return _cached_excel(self.scope, file, self._client)

    def clear_cache(self):
        for config in self._configs:
            _cached_inventory.clear(self.scope, config, self._client)
        for file in self._files:
            _cached_excel.clear(self.scope, file, self._client)
        self._files.clear()


def _parse_account(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise DriveError("drive.service_account_json không phải JSON hợp lệ. Dán nguyên JSON vào chuỗi TOML ba dấu nháy đơn.") from None
    if not isinstance(value, Mapping):
        raise DriveError("Thiếu drive.service_account_json trong Streamlit Secrets.")
    info = dict(value)
    required = {"type", "client_email", "private_key", "token_uri"}
    if info.get("type") != "service_account" or any(not isinstance(info.get(key), str) or not info[key].strip() for key in required):
        raise DriveError("drive.service_account_json cần là JSON Service Account đầy đủ, gồm type, client_email, private_key và token_uri.")
    return info


def _read_configuration(secrets):
    if "drive" in secrets:
        section = secrets["drive"]
        if not isinstance(section, Mapping):
            raise DriveError("Secrets cần bảng [drive] với folder_id và service_account_json.")
        if not section.get("folder_id"):
            raise DriveError("Thiếu drive.folder_id trong Streamlit Secrets.")
        info = _parse_account(section.get("service_account_json"))
        config = DriveConfig.from_mapping({
            key: section[key] for key in ("folder_id", "reference_file_id", "data_subfolder", "recursive") if key in section
        } | {"refresh_seconds": CACHE_TTL_SECONDS})
        return config, info
    # Keep existing deployed configurations working during migration.
    if "gcp_service_account" in secrets:
        config = DriveConfig.from_mapping(dict(secrets.get("google_drive", {})) | {"refresh_seconds": CACHE_TTL_SECONDS})
        return config, _parse_account(secrets["gcp_service_account"])
    raise DriveError("Chưa cấu hình Google Drive. Thêm [drive] gồm folder_id và service_account_json vào Settings → Secrets của Streamlit.")


def _configuration_from_secrets(secrets=None):
    try:
        secrets = st.secrets if secrets is None else secrets
        config, info = _read_configuration(secrets)
    except (FileNotFoundError, StreamlitSecretNotFoundError):
        raise DriveError("Chưa có Streamlit Secrets. Cấu hình [drive] với folder_id và service_account_json.") from None
    return config, info


def _namespace(config, info):
    # Only an opaque digest reaches cache keys or the temporary cache path.
    return hashlib.sha256(json.dumps({"account": info, "folder": config.folder_id,
                                     "reference": config.reference_file_id}, sort_keys=True).encode()).hexdigest()


def drive_cache_namespace(secrets=None):
    return _namespace(*_configuration_from_secrets(secrets))


def create_drive_repository(settings, secrets=None):
    config, info = _configuration_from_secrets(secrets)
    scope = _namespace(config, info)
    try:
        api = DriveClient.from_service_account(info, settings.max_upload_mb * 1024 * 1024)
    except DriveError:
        raise DriveError("Không khởi tạo được Service Account. Kiểm tra drive.service_account_json và định dạng private_key trong Streamlit Secrets.") from None
    client = CachedDriveClient(api, scope)
    scoped_settings = replace(settings, local_data_dir=Path(settings.local_data_dir) / ("drive-" + scope[:24]))
    return DriveRepository(scoped_settings, config, client)
