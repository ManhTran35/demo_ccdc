"""One-time Excel ingestion; dashboards read normalized Parquet snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import io
import math

import pandas as pd

import core
from services.validation import ValidationError, validate_workbook


@dataclass(frozen=True)
class TransformResult:
    dataframe: pd.DataFrame
    parquet_bytes: bytes
    report: dict
    metrics: dict


DEFAULT_WARNING_LIMITS = {
    "missing_date": 0.25,
    "invalid_date": 0.01,
    "future_date": 0.01,
    "duplicate_serial_rows": 0.05,
    "duplicate_rows": 0.05,
    "unclassified_rows": 0.05,
    "review_rows": 0.05,
    "missing_code": 0.0,
    "missing_name": 0.0,
    "unknown_status": 0.0,
    "unknown_category": 0.0,
}
CANONICAL_STATUSES = {"Đang sử dụng", "Hỏng", "Tốt", "Tiêu hao đã xử lý", "Xuất cho đơn vị"}
CANONICAL_CATEGORIES = {"Thiết bị", "Vật tư"}


def _table(content, filename, settings, classification=False):
    validate_workbook(content, filename, settings, classification=classification)
    try:
        result = core.read_table(content, classification=classification)
    except Exception:
        # All parser failures are validation failures after staging; keep their
        # details out of logs because third-party exceptions can include cells.
        raise ValidationError("Không đọc được bảng Excel theo template (dòng tiêu đề phải nằm trong 30 dòng đầu).", code="table_schema") from None
    if len(set(result["columns"])) != len(result["columns"]):
        raise ValidationError("Tên cột Excel trùng nhau sau khi phân biệt tiêu đề lặp; hãy kiểm tra template.", code="duplicate_columns")
    max_rows = getattr(settings, "max_rows", 250000)
    max_columns = getattr(settings, "max_columns", 200)
    if len(result["rows"]) > max_rows or len(result["columns"]) > max_columns:
        raise ValidationError("Bảng Excel vượt giới hạn số dòng/cột đã cấu hình.", code="table_limit")
    return result


def parse_classification(content: bytes, settings=None, filename="classification.xlsx"):
    """Return canonical classification dictionary and non-sensitive audit counts."""
    table = _table(content, filename, settings, classification=True)
    try:
        mapping, report = core.classification_map(table)
    except ValueError as exc:
        # The core error names only missing canonical columns, not uploaded values.
        raise ValidationError(str(exc), code="classification_schema") from None
    if not table["rows"]:
        raise ValidationError("Bảng phân loại không có bản ghi dữ liệu.", code="empty_classification")
    report.update(valid=True, row_count=len(table["rows"]), errors=[])
    return mapping, report


def _check_mapping(mapping):
    if not isinstance(mapping, dict):
        raise ValidationError("Mapping phân loại phải là dictionary đã được kiểm tra.", code="invalid_mapping")
    if any(not isinstance(k, str) or not isinstance(v, (list, tuple)) or len(v) != 4
           or any(not isinstance(item, str) for item in v) for k, v in mapping.items()):
        raise ValidationError("Mapping phân loại có cấu trúc không hợp lệ.", code="invalid_mapping")


def snapshot_metrics(df):
    """Extend existing KPI definitions with explicit unit and row counts.

    Counts of organizational units use the same normalized manager column shown
    by the dashboard. Quantities remain grouped by ĐVT, never summed across units.
    No physical-device identity or transaction deduplication is inferred.
    """
    result = core.metrics(df)
    result["units"] = int(df.loc[df["Đơn vị quản lý"].ne(core.UNKNOWN), "Đơn vị quản lý"].nunique())
    result["branches"] = int(df.loc[df["Chi nhánh"].ne(core.UNKNOWN), "Chi nhánh"].nunique())
    result["status_rows"] = {str(k): int(v) for k, v in df["Tình trạng"].value_counts().sort_index().items()}
    result["quantity_by_unit"] = [
        {"unit": str(unit), "rows": int(len(group)), "quantity": float(group["Số lượng"].sum())}
        for unit, group in df.groupby("ĐVT", sort=True, dropna=False)
    ]
    return result


def compare_metrics(candidate, previous=None):
    """Preview only comparable numeric KPIs; do not invent missing business KPI."""
    previous = previous or {}
    keys = ("rows", "units", "branches", "codes", "using_codes", "material_missing_codes",
            "device_rows", "broken_rows", "broken_rate")
    return {key: {"before": previous.get(key), "after": candidate.get(key),
                  "delta": (candidate[key] - previous[key])
                  if isinstance(candidate.get(key), (int, float))
                  and isinstance(previous.get(key), (int, float)) else None}
            for key in keys}


def _warnings(report, settings):
    limits = DEFAULT_WARNING_LIMITS.copy()
    limits.update(getattr(settings, "warning_limits", {}) or {})
    warnings = []
    for key, limit in limits.items():
        if key not in DEFAULT_WARNING_LIMITS:
            raise ValidationError("Cấu hình có tên ngưỡng cảnh báo không hợp lệ.", code="warning_configuration")
        if not isinstance(limit, (float, int)) or not math.isfinite(limit) or not 0 <= limit <= 1:
            raise ValidationError("Ngưỡng cảnh báo phải là tỷ lệ từ 0 tới 1.", code="warning_configuration")
        count = report.get(key, 0)
        ratio = count / report["source_rows"] if report["source_rows"] else 0
        if count and ratio > limit:
            warnings.append({"code": key, "count": int(count), "ratio": ratio, "limit": limit})
    return warnings


def transform_inventory(content: bytes, mapping: dict, today: date | None = None,
                        settings=None, filename="source.xlsx") -> TransformResult:
    """Validate and normalize once, keeping every source row and source column.

    ``Gốc ·`` columns are strings to make mixed Excel types interoperable with
    Arrow. String contents, source row numbers and identifier zeros survive;
    exact binary cell styles/types remain available in the immutable raw XLSX.
    Invalid numeric quantities/required schemas block snapshot publication.
    """
    today = today or core.vn_today()
    _check_mapping(mapping)
    table = _table(content, filename, settings)
    try:
        frame, report = core.normalize(table, mapping, today)
    except ValueError as exc:
        raise ValidationError(str(exc), code="inventory_schema") from None
    report.update(valid=not bool(report["errors"]), row_count=len(frame))
    report["unknown_status"] = int((~frame["Tình trạng"].isin(CANONICAL_STATUSES)).sum())
    report["unknown_category"] = int((~frame["Chủng loại"].isin(CANONICAL_CATEGORIES)).sum())
    report["warnings"] = _warnings(report, settings)
    report["normalization_schema"] = 1
    report["raw_columns_encoding"] = "string; exact XLSX retained separately"
    if report["errors"]:
        raise ValidationError(" ".join(report["errors"]), code="inventory_domain", report=report)
    for column in frame.columns:
        if column.startswith("Gốc · "):
            frame[column] = frame[column].map(lambda value: "" if value is None else str(value))
    # The normalized IDs and serials are always strings, independent of dtype
    # inference, and XLSX number_format zeros were already handled by core.
    for column in ("Mã VTTB", "Serial", "Mã đơn vị", "Mã nhân viên"):
        frame[column] = frame[column].astype("string")
    buffer = io.BytesIO()
    frame.to_parquet(buffer, engine="pyarrow", index=False, compression="snappy")
    return TransformResult(frame, buffer.getvalue(), report, snapshot_metrics(frame))


def refresh_dynamic_fields(df, today=None):
    """Recompute only daily age/date fields, retaining normalized classifications."""
    return core.refresh_dynamic_fields(df, today or core.vn_today())
