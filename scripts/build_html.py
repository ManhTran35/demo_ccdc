"""Build a self-contained HTML with actual source data and offline XLSX support."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import SAMPLE_CUTOFF, classification_map, read_table


def build(target=None):
    source = (ROOT / "data" / "samples" / "ccdc.xlsx").read_bytes()
    table = read_table(source)
    mapping, _ = classification_map(read_table((ROOT / "data" / "samples" / "classification.xlsx").read_bytes(), True))
    payload = {"id": hashlib.sha256(source).hexdigest()[:32], "name": "file ccdc.xlsx", "cutoff": SAMPLE_CUTOFF, "created_at": "2026-10-13T00:00:00.000Z", "published_at": "2026-10-13T00:00:00.000Z", "table": table, "mapping": mapping, "note": "Phiên mẫu từ file người dùng cung cấp; ngày chốt đã xác nhận."}
    data = base64.b64encode(gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)).decode()
    html = (ROOT / "assets" / "dashboard.template.html").read_text(encoding="utf-8")
    for placeholder, value in [
        ("/*__CSS__*/", (ROOT / "assets" / "dashboard.css").read_text(encoding="utf-8")),
        ("__DATA__", data),
        ("__ORIGINAL__", base64.b64encode(source).decode()),
        ("/*__XLSX__*/", (ROOT / "assets" / "xlsx.full.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")),
        ("/*__JS__*/", (ROOT / "assets" / "dashboard.js").read_text(encoding="utf-8")),
    ]:
        html = html.replace(placeholder, value)
    target = Path(target or ROOT / "dist" / "dashboard_ccdc.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    print(f"Built {target.name}: {len(table['rows']):,} rows, {target.stat().st_size / 1024 / 1024:.2f} MiB")
    return target


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
