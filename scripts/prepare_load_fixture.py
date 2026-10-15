#!/usr/bin/env python3
"""Prepare a synthetic local snapshot and expected metrics for browser load tests.

From the repository root (with development dependencies installed):
    python scripts/prepare_load_fixture.py --rows 100000 \
        --data-dir /tmp/ccdc-load/store --output /tmp/ccdc-load/expected.json
    APP_ENV=local STORAGE_BACKEND=local LOCAL_DATA_DIR=/tmp/ccdc-load/store \
        python -m streamlit run app.py --server.port=8502
    node scripts/check_concurrency.cjs --origin http://127.0.0.1:8502 \
        --expected /tmp/ccdc-load/expected.json --output /tmp/ccdc-load/results.json

Use a fresh data directory/output for each run. This command never reads real
Excel, uses GCS, or changes existing snapshots. Cloud configuration and ADC are
ignored. The load test is local synthetic evidence, not an IAP/Cloud Run test.
"""
from __future__ import annotations

import argparse
from datetime import date
import io
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import core
from scripts.generate_demo_data import synthetic_files
from services.config import Settings
from services.storage import LocalStorageAdapter
from services.transform import parse_classification, snapshot_metrics, transform_inventory
from services.versions import VersionManager


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Ngày phải có định dạng YYYY-MM-DD.") from None


def _rows(value):
    try:
        result = int(value)
        if not 1 <= result <= 250000:
            raise ValueError()
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Số dòng phải từ 1 đến 250.000.") from None


def prepare(rows, data_dir, output, data_date):
    """Run the actual ingestion/publication path with generated fixtures only."""
    started = time.perf_counter()
    data_dir = Path(data_dir).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new --output path.")
    if output in {data_dir / name for name in ("objects.sqlite3", "objects.sqlite3-wal", "objects.sqlite3-shm")}:
        raise ValueError("Output must not use a reserved local-store database filename.")
    # Construct explicitly: do not load environment variables or call a storage
    # factory, so even a cloud-configured shell cannot select GCS accidentally.
    storage = LocalStorageAdapter(data_dir)
    if storage.list():
        raise ValueError("Data store contains objects; choose a fresh --data-dir. Existing data was preserved.")
    settings = Settings(app_env="local", storage_backend="local", local_data_dir=data_dir)
    manager = VersionManager(storage)
    actor = "local-admin (mô phỏng)"
    today = core.vn_today()
    source, reference = synthetic_files(rows)
    source_stage = manager.stage_upload(source, f"inventory_demo_{rows}.xlsx", actor)
    reference_stage = manager.stage_upload(reference, "classification_demo.xlsx", actor)
    mapping, classification_report = parse_classification(reference, settings)
    manager.record_stage_validation(reference_stage["id"], classification_report)
    result = transform_inventory(source, mapping, today, settings)
    report = result.report.copy()
    report["classification_stage_id"] = reference_stage["id"]
    report["classification"] = classification_report
    manager.record_stage_validation(source_stage["id"], report)
    preview = manager.prepare_version(
        source_stage, result.parquet_bytes, mapping, report, result.metrics,
        data_date.isoformat(), actor, 0,
        note=f"Synthetic {rows} rows for local browser-session load verification.")
    pointer = manager.commit(preview["id"], preview["confirmation_token"], actor, 0)
    version = pointer["active_version"]
    if version != preview["id"] or manager.read_classification_source(version) != reference:
        raise RuntimeError("Published fixture readback did not match its source.")
    # Expected browser values come from the persisted, checksummed Parquet,
    # rather than a second parser or a separately reconstructed dataset.
    frame = pd.read_parquet(io.BytesIO(manager.read_dataset(version)))
    metrics = snapshot_metrics(frame)
    if len(frame) != rows or metrics != manager.read_metrics(version):
        raise RuntimeError("Published fixture row count/KPI readback did not match.")
    branches = [{"branch": str(branch), "rows": len(group), "metrics": snapshot_metrics(group)}
                for branch, group in frame.groupby("Chi nhánh", sort=True)]
    expected = {
        "synthetic_only": True,
        "local_data_dir": str(data_dir),
        "data_date": data_date.isoformat(),
        "age_calculation_date": today.isoformat(),
        "active_version": version,
        "version_id": version,
        "generation": pointer["_generation"],
        "metrics": metrics,
        "row_count": len(frame),
        "rows": len(frame),
        "branches": branches,
        "branch_values": [item["branch"] for item in branches],
        "branch_counts": {item["branch"]: item["rows"] for item in branches},
        "branch_metrics": {item["branch"]: item["metrics"] for item in branches},
        "inventory_xlsx_bytes": len(source),
        "classification_xlsx_bytes": len(reference),
        "parquet_bytes": len(result.parquet_bytes),
        "preparation_seconds": round(time.perf_counter() - started, 4),
        "pipeline": "synthetic_files -> stage originals -> validate/normalize/Parquet -> immutable snapshots/readback -> token-confirmed CAS commit",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create also protects against another process writing this path
    # while fixture generation runs. Never truncate an existing output file.
    with output.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(expected, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=_rows, default=100000)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data-date", type=_date, default=core.vn_today())
    args = parser.parse_args(argv)
    try:
        result = prepare(args.rows, args.data_dir, args.output, args.data_date)
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps({"ready": True, "synthetic_only": True, "rows": result["rows"],
                      "active_version": result["active_version"], "data_date": result["data_date"],
                      "branches": len(result["branches"]), "preparation_seconds": result["preparation_seconds"],
                      "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
