"""Dashboard access to immutable normalized snapshots (no Excel on read)."""
from datetime import date
import io

import pandas as pd

from core import refresh_dynamic_fields
from services.config import Settings, create_storage
from services.storage import PreconditionFailed
from services.versions import VersionManager


class DataRepository:
    def __init__(self, settings=None, storage=None):
        self.settings = settings or Settings.from_env()
        self.manager = VersionManager(storage if storage is not None else create_storage(self.settings))

    def get_active_version(self):
        return self.manager.get_active()

    def read_dataset(self, version_id, today):
        frame = pd.read_parquet(io.BytesIO(self.manager.read_dataset(version_id)))
        return refresh_dynamic_fields(frame, today)

    def read_active_dataset(self, today):
        active = self.get_active_version()
        return self.read_dataset(active["active_version"], today) if active else None

    def select_version(self, cutoff, active=None):
        """The official active pointer bounds normal dashboard browsing.

        A date at/after the active data date uses active, including after rollback.
        Earlier cutoffs browse previously published snapshots, never prepared ones.
        """
        active = active if active is not None else self.get_active_version()
        if active is None:
            return None
        if str(cutoff) >= active["data_date"]:
            return {"id": active["active_version"], "cutoff": active["data_date"], "published_at": active["published_at_utc"]}
        versions = [v for v in self.manager.list_versions() if v["published"] and v["data_date"] <= str(cutoff)]
        if not versions:
            return None
        selected = max(versions, key=lambda v: (v["data_date"], v["publication_order"]))
        return {"id": selected["id"], "cutoff": selected["data_date"], "published_at": selected["published_at_utc"]}

    def list_versions(self):
        return self.manager.list_versions()

    def seed_synthetic_demo(self):
        if self.settings.cloud_mode or self.settings.drive_mode:
            return  # No default files or synthetic fallback in production/staging.
        if self.manager.storage.list(""):
            return
        from services.demo import synthetic_files
        from services.transform import parse_classification, transform_inventory
        inventory, classification = synthetic_files(1000)
        mapping, classification_report = parse_classification(classification, self.settings)
        result = transform_inventory(inventory, mapping, settings=self.settings)
        classification_stage = self.manager.stage_upload(classification, "classification_demo.xlsx", "local-admin (mô phỏng)")
        self.manager.record_stage_validation(classification_stage["id"], classification_report)
        result.report["classification_stage_id"] = classification_stage["id"]
        stage = self.manager.stage_upload(inventory, "inventory_demo.xlsx", "local-admin (mô phỏng)")
        self.manager.record_stage_validation(stage["id"], result.report)
        try:
            preview = self.manager.prepare_version(stage, result.parquet_bytes, mapping, result.report, result.metrics,
                                                   "2026-10-13", "local-admin (mô phỏng)", 0,
                                                   note="DỮ LIỆU GIẢ cho demo local, không phải số liệu doanh nghiệp.")
            self.manager.commit(preview["id"], preview["confirmation_token"], "local-admin (mô phỏng)", 0)
        except PreconditionFailed:
            if not self.manager.get_active():
                raise


def live_quality_report(frame, stored, today):
    report = dict(stored)
    for key, label in [("missing_date", "Thiếu ngày"), ("invalid_date", "Ngày không hợp lệ"), ("future_date", "Ngày ở tương lai")]:
        report[key] = int(frame["Trạng thái ngày"].eq(label).sum())
    report["today"] = today.isoformat()
    return report
