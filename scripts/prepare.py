"""Prepare synthetic local snapshots only; cloud never auto-seeds data."""
from pathlib import Path
import sys
import json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import metrics, vn_today
from services.config import Settings
from services.repository import DataRepository
settings=Settings.from_env()
if settings.cloud_mode:
    raise SystemExit("Cloud không tự seed. Dùng ingest_excel.py preview rồi publish với Admin ADC.")
repo=DataRepository(settings)
repo.seed_synthetic_demo()
active=repo.get_active_version()
if not active:raise SystemExit("Chưa có phiên; dùng script ingestion hoặc Quản trị local.")
frame=repo.read_dataset(active['active_version'],vn_today())
print(json.dumps({'active_version':active['active_version'],'cutoff':active['data_date'],'metrics':metrics(frame)},ensure_ascii=False,indent=2))
