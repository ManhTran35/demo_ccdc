"""Deterministic synthetic Excel fixtures. No enterprise records are used."""
from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openpyxl import Workbook

from services.demo import synthetic_files


def write_demo_files(output, rows=1000):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inventory, classification = synthetic_files(rows)
    (output / 'inventory_demo.xlsx').write_bytes(inventory)
    (output / 'classification_demo.xlsx').write_bytes(classification)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Tạo Excel giả cho local/staging; không đọc dữ liệu thật.')
    parser.add_argument('--rows', type=int, default=1000)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'generated')
    args = parser.parse_args()
    target = write_demo_files(args.output, args.rows)
    print(f'Đã tạo {args.rows:,} dòng dữ liệu giả tại {target}.')
