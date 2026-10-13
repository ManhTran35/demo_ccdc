"""Prepare persistent sample data once and verify the active snapshot."""
from pathlib import Path
import sys
import json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import VersionStore, seed_store, normalize, metrics, vn_today

store=VersionStore()
seed_store(store)
version=store.latest()
if not version:
    raise SystemExit("Chưa có phiên đã công bố; mở Quản trị để duyệt.")
payload=store.load(version['id'])
df,report=normalize(payload['table'],payload['mapping'],vn_today())
if report['errors']:
    raise SystemExit('; '.join(report['errors']))
print(json.dumps({'cutoff':version['cutoff'],'quality':report,'metrics':metrics(df)},ensure_ascii=False,indent=2))
