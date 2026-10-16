#!/usr/bin/env python3
"""V1-A trusted-admin ingestion. Preview is the default; publish is explicit.

Cloud identity comes from verified ADC and ADMIN_EMAILS, never --actor or an
HTTP header. This command does not create buckets or other cloud resources.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import auth, config
from services.transform import compare_metrics, parse_classification, transform_inventory
from services.validation import ValidationError
from services.versions import VersionManager


NUMERIC_KPIS = ("rows", "units", "branches", "codes", "using_codes", "material_missing_codes", "device_rows", "broken_rows", "broken_rate")


def _date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("Ngày phải có định dạng YYYY-MM-DD.") from None


def _generation(value):
    try:
        result = int(value)
        if result < 0:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Generation phải là số nguyên không âm.") from None


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", help="Stage Excel, validate and preview. Does not publish.")
    preview.add_argument("--file", required=True, type=Path)
    preview.add_argument("--classification", type=Path, help="Required for the first version; otherwise reuse active mapping.")
    preview.add_argument("--data-date", required=True, type=_date)
    publish = sub.add_parser("publish", help="Publish the exact previously previewed immutable version.")
    publish.add_argument("--version", required=True)
    publish.add_argument("--confirm", required=True, help="Exact confirmation_token returned by preview.")
    publish.add_argument("--expected-generation", required=True, type=_generation)
    sub.add_parser("list", help="List versions; does not change the active pointer.")
    rollback = sub.add_parser("rollback", help="Preview rollback unless both confirmation and generation are supplied.")
    rollback.add_argument("--version", required=True)
    rollback.add_argument("--confirm")
    rollback.add_argument("--expected-generation", type=_generation)
    return command


def _version_id(value):
    return value.get("id") or value.get("version_id") or value.get("active_version")


def _active_generation(active):
    return int(active["_generation"]) if active else 0


def _read_local(path, settings):
    # Refuse unbounded reads. Rejected oversize originals remain untouched at
    # their local path; every bounded upload, including corrupt XLSX, is staged.
    if not path.is_file():
        raise ValidationError("Không tìm thấy file Excel local.", code="file_not_found")
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError("File vượt giới hạn dung lượng; bản gốc vẫn được giữ nguyên tại máy local.", code="upload_limit")
    with path.open("rb") as source:
        content = source.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError("File thay đổi kích thước khi đang đọc; hãy tạo preview mới.", code="source_changed")
    return content


def _validation_summary(report):
    return {key: value for key, value in report.items()
            if isinstance(value, (int, float, bool)) or key in {"errors", "warnings", "today", "code"}}


def preview_file(args, settings, manager, actor):
    # Take the active generation before any staging/parse work, so a concurrent
    # publication invalidates this preview rather than silently rebasing it.
    active = manager.get_active()
    expected = _active_generation(active)
    content = _read_local(args.file, settings)
    stage = manager.stage_upload(content, args.file.name, actor)
    classification_stage = None
    classification_stage_id = None
    try:
        if args.classification:
            classification_content = _read_local(args.classification, settings)
            classification_stage = manager.stage_upload(classification_content, args.classification.name, actor)
            classification_stage_id = classification_stage["id"]
            mapping, classification_report = parse_classification(
                classification_content, settings, args.classification.name)
            manager.record_stage_validation(classification_stage["id"], classification_report)
        elif active:
            mapping = manager.read_mapping(_version_id(active))
            classification_stage_id = manager.read_validation(_version_id(active)).get("classification_stage_id")
            classification_report = {"reused_active_mapping": True, "valid": True, "errors": []}
        else:
            raise ValidationError("Phiên đầu tiên cần --classification để kiểm tra bảng phân loại.", code="missing_classification")
        # Today's age is independent of --data-date, using Asia/Ho_Chi_Minh.
        transformed = transform_inventory(content, mapping, settings=settings, filename=args.file.name)
        report = transformed.report.copy()
        report["classification"] = classification_report
        if classification_stage_id:
            report["classification_stage_id"] = classification_stage_id
        previous = manager.read_metrics(_version_id(active)) if active else None
        comparison = compare_metrics(transformed.metrics, previous)
        report["kpi_comparison"] = comparison
        manager.record_stage_validation(stage["id"], report)
        preview = manager.prepare_version(
            stage, transformed.parquet_bytes, mapping, report, transformed.metrics,
            args.data_date, actor, expected)
        return {"operation": "preview", "published": False, "version": _version_id(preview),
                "data_date": args.data_date, "expected_generation": expected,
                "confirmation_token": preview["confirmation_token"],
                "validation": _validation_summary(report), "comparison": comparison,
                "notice": "Xem KPI và cảnh báo, sau đó dùng publish với đúng version/token/generation này."}
    except ValidationError as exc:
        report = exc.report.copy()
        if classification_stage:
            report["classification_stage_id"] = classification_stage["id"]
            # A failed classification is retained alongside its exact original.
            try:
                manager.record_stage_validation(classification_stage["id"], report)
            except Exception:
                pass  # stage bytes remain; original failure must stay actionable.
        try:
            manager.record_stage_validation(stage["id"], report)
        except Exception:
            pass
        report["stage_id"] = stage["id"]
        exc.report = report
        raise


def execute(args, settings, manager, actor):
    if args.command == "preview":
        return preview_file(args, settings, manager, actor)
    if args.command == "publish":
        pointer = manager.commit(args.version, args.confirm, actor, args.expected_generation)
        return {"operation": "publish", "version": _version_id(pointer), "generation": pointer["_generation"]}
    if args.command == "list":
        versions = []
        for item in manager.list_versions():
            metrics = manager.read_metrics(_version_id(item))
            versions.append({"version": _version_id(item), "data_date": item.get("data_date"),
                             "published": bool(item.get("published")), "current": bool(item.get("current")),
                             "metrics": {key: metrics.get(key) for key in NUMERIC_KPIS}})
        return {"operation": "list", "versions": versions}
    if args.command == "rollback":
        if args.confirm:
            if args.expected_generation is None:
                raise ValidationError("Rollback xác nhận cần --expected-generation từ preview.", code="missing_generation")
            pointer = manager.rollback(args.version, actor, args.expected_generation, args.confirm)
            return {"operation": "rollback", "version": _version_id(pointer), "generation": pointer["_generation"]}
        generation = args.expected_generation
        if generation is None:
            generation = _active_generation(manager.get_active())
        preview = manager.preview_rollback(args.version, generation)
        return {"operation": "rollback_preview", "published": False, "version": args.version,
                "expected_generation": generation, "confirmation_token": preview["confirmation_token"]}
    raise ValueError("Unsupported command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settings = config.Settings.from_env()
        actor = auth.resolve_cli_actor(settings)
        manager = VersionManager(config.create_storage(settings))
        result = execute(args, settings, manager, actor)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except ValidationError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error_id": uuid.uuid4().hex,
                          "message": str(exc), "validation": _validation_summary(exc.report),
                          "stage_id": exc.report.get("stage_id")}, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 2
    except Exception as exc:
        # Do not emit exception text/tracebacks: cloud SDK errors can contain
        # request headers, credential paths, URLs, or uploaded cell contents.
        print(json.dumps({"ok": False, "error_id": uuid.uuid4().hex,
                          "code": type(exc).__name__,
                          "message": "Thao tác thất bại; active version không bị thay đổi nếu chưa CAS thành công. Kiểm tra quyền/cấu hình và tạo preview mới."}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
