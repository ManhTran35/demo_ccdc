#!/usr/bin/env python3
"""Profile 100k synthetic records in a fresh process; never opens real data.

Run: python scripts/benchmark_ingestion.py --rows 100000 --output /tmp/ingestion-benchmark.json
RSS is the cumulative process peak (Linux ru_maxrss), not a Cloud Run guarantee.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import date, timedelta
import gc
import io
import json
from pathlib import Path
import platform
import resource
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import core
from scripts.generate_demo_data import synthetic_files
from services.config import Settings
import services.transform as transform


def profile(rows):
    started = time.perf_counter()
    timings = {}
    peaks = {}

    def peak():
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)

    def run(name, action):
        tick = time.perf_counter()
        result = action()
        timings[name] = round(time.perf_counter() - tick, 4)
        peaks[name] = peak()
        return result

    original, classification = run("synthetic_xlsx_generation", lambda: synthetic_files(rows))
    gc.collect()
    mapping, reference_report = run("classification_security_and_parse", lambda: transform.parse_classification(classification, Settings()))
    # Attribute the real transform path, retaining exactly its implementation.
    phase_parts = {}

    def measured(name, function):
        def wrapped(*args, **kwargs):
            tick = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                phase_parts[name] = round(phase_parts.get(name, 0) + time.perf_counter() - tick, 4)
        return wrapped

    today = date(2026, 10, 15)
    with ExitStack() as stack:
        stack.enter_context(patch.object(transform, "validate_workbook", measured("xlsx_security_validation", transform.validate_workbook)))
        stack.enter_context(patch.object(core, "read_table", measured("xlsx_parse", core.read_table)))
        stack.enter_context(patch.object(core, "normalize", measured("canonical_normalization", core.normalize)))
        stack.enter_context(patch.object(pd.DataFrame, "to_parquet", measured("parquet_write", pd.DataFrame.to_parquet)))
        result = run("inventory_transform_total", lambda: transform.transform_inventory(original, mapping, today, Settings()))
    frame = result.dataframe
    parquet = result.parquet_bytes
    gc.collect()
    restored = run("parquet_read", lambda: pd.read_parquet(io.BytesIO(parquet)))
    assert len(restored) == rows == result.report["row_count"]
    assert core.metrics(restored) == core.metrics(frame)
    assert restored["Mã VTTB"].equals(frame["Mã VTTB"])
    del restored
    gc.collect()
    refreshed = run("daily_age_refresh", lambda: transform.refresh_dynamic_fields(frame, today + timedelta(days=1)))
    assert refreshed["Loại VTTB"].equals(frame["Loại VTTB"])
    assert refreshed["Serial"].equals(frame["Serial"])
    del refreshed
    gc.collect()
    metrics = run("metrics", lambda: transform.snapshot_metrics(frame))
    filtered = run("combined_filters_search", lambda: core.apply_filters(frame, branches=["CN01"], statuses=["Đang sử dụng"], search="Thiết bị mẫu"))
    filtered_rows = len(filtered)
    del filtered
    export = run("export_frame", lambda: core.export_frame(frame, core.DETAIL_COLUMNS + ["Mã đơn vị", "Serial"], "2026-10-13", today))
    csv_size = run("export_csv_all_rows", lambda: len(core.export_csv(export)))
    gc.collect()
    xlsx_size = run("export_excel_all_rows", lambda: len(core.export_excel(export)))
    gc.collect()
    return {
        "synthetic_only": True,
        "rows": rows,
        "normalized_columns": len(frame.columns),
        "raw_columns": sum(column.startswith("Gốc · ") for column in frame.columns),
        "xlsx_bytes": len(original),
        "classification_xlsx_bytes": len(classification),
        "parquet_bytes": len(parquet),
        "dataframe_deep_bytes": int(frame.memory_usage(index=True, deep=True).sum()),
        "csv_export_bytes": csv_size,
        "xlsx_export_bytes": xlsx_size,
        "filtered_rows": filtered_rows,
        "timings_seconds": timings,
        "transform_phases_seconds": phase_parts,
        "cumulative_peak_rss_mib_by_phase": peaks,
        "peak_rss_mib": peak(),
        "below_2gib_process_peak": peak() < 2048,
        "total_seconds": round(time.perf_counter() - started, 4),
        "metrics": metrics,
        "classification_counts": reference_report,
        "python": platform.python_version(),
        "platform": platform.system(),
        "limitations": ["Fresh local Python process; synthetic fixtures do not represent all production files.",
                        "Single-ingestion process only; does not establish five-user or container memory capacity.",
                        "RSS is a cumulative peak and includes synthetic XLSX generation and full exports.",
                        "2 GiB remains a proposal pending deployed staging tests with real access controls."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--output", type=Path, default=Path("/tmp/ingestion-benchmark.json"))
    args = parser.parse_args()
    result = profile(args.rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"synthetic_only": True, "rows": args.rows, "peak_rss_mib": result["peak_rss_mib"], "total_seconds": result["total_seconds"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
