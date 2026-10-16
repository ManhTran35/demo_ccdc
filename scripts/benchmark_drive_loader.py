#!/usr/bin/env python3
"""Profile real loader/cache/Pandas/UI using synthetic Excel and a fake Drive API."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
from streamlit.testing.v1 import AppTest
from core import metrics, vn_today
from services.config import Settings
from services.demo import synthetic_files
from services.drive import DriveFile, FOLDER_ID, REFERENCE_ID, XLSX
from services.drive_loader import create_drive_repository


class FixtureAPI:
    def __init__(self, inventory, reference):
        stamp=datetime.now(timezone.utc).isoformat()
        self.contents={'benchmark_inventory':inventory,REFERENCE_ID:reference}
        self.inventory=DriveFile('benchmark_inventory',f'Ton_CCDC_{vn_today():%d%m%Y}.xlsx',XLSX,
                                 stamp,'1',hashlib.md5(inventory).hexdigest(),len(inventory))
        self.reference=DriveFile(REFERENCE_ID,'Phan_loai_VTTB.xlsx',XLSX,
                                 stamp,'1',hashlib.md5(reference).hexdigest(),len(reference))
        self.list_calls=0
        self.download_calls=0

    def list_inventory(self, config):
        self.list_calls+=1
        return [self.inventory]

    def metadata(self, file_id):
        return self.reference if file_id==REFERENCE_ID else self.inventory

    def download(self, file):
        self.download_calls+=1
        return self.contents[file.id]


def profile(rows):
    timings={}
    def run(name, action):
        tick=time.perf_counter()
        result=action()
        timings[name]=round(time.perf_counter()-tick,4)
        print(json.dumps({'completed':name,'seconds':timings[name]}),flush=True)
        return result

    inventory,reference=run('generate_synthetic_xlsx',lambda:synthetic_files(rows))
    api=FixtureAPI(inventory,reference)
    account={'type':'service_account','client_email':'benchmark@example.iam.gserviceaccount.com',
             'private_key':'BENCHMARK_PLACEHOLDER','token_uri':'https://oauth2.googleapis.com/token'}
    config={'drive':{'folder_id':FOLDER_ID,'reference_file_id':REFERENCE_ID,'service_account_json':account}}
    st.cache_data.clear();st.cache_resource.clear();gc.collect()
    with tempfile.TemporaryDirectory() as directory:
        settings=replace(Settings(),app_env='community-drive',local_data_dir=Path(directory))
        with patch('services.drive_loader.DriveClient.from_service_account',return_value=api):
            repo=create_drive_repository(settings,config)
        run('cold_download_validation_pandas_parquet_cache',repo.synchronize)
        assert not repo.last_error,repo.last_error
        frame=run('read_pandas_dataset',lambda:repo.read_active_dataset(vn_today()))
        assert len(frame)==rows
        assert frame['Mã VTTB'].str.fullmatch(r'\d{6}').all()
        expected=metrics(frame)
        dataframe_bytes=int(frame.memory_usage(index=True,deep=True).sum())
        del frame;gc.collect()
        def warm_read():
            repo.client.list_inventory(repo.config)
            repo.client.download(api.inventory)
            repo.client.download(api.reference)
        run('warm_cached_index_and_excel',warm_read)
        assert api.list_calls==1 and api.download_calls==2
        with patch('streamlit.secrets',config), patch.dict(os.environ,{'APP_ENV':'community-drive','STORAGE_BACKEND':'local'}), \
                patch('services.drive_loader.create_drive_repository',return_value=repo):
            app=run('dashboard_overview_100k',lambda:AppTest.from_file(str(ROOT/'streamlit_app.py'),default_timeout=180).run())
            assert not app.exception and not app.error
            assert len(app.metric)==3 and app.metric[0].value==f"{expected['using_codes']:,}".replace(',','.')
            run('filter_branch',lambda:app.sidebar.multiselect[0].set_value(['CN01']).run())
            run('details',lambda:app.sidebar.radio[0].set_value('Chi tiết dữ liệu').run())
            run('filtered_csv_excel',lambda:next(b for b in app.button if 'Chuẩn bị CSV' in b.label).click().run())
            assert not app.exception and not app.error and len(app.get('download_button'))==2
        result={'synthetic_only':True,'api':'mocked; no live Google request','python':platform.python_version(),
                'rows':rows,'xlsx_bytes':len(inventory),'dataframe_deep_bytes':dataframe_bytes,
                'timings_seconds':timings,'drive_list_calls':api.list_calls,'drive_download_calls':api.download_calls,
                'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
                'dashboard_verified':True,'filtered_export_rows':(rows+9)//10,'metrics':expected,
                'limitations':['Local process and synthetic fixture; actual Community Cloud capacity/network not verified.']}
    st.cache_data.clear();st.cache_resource.clear()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--rows',type=int,default=100000)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=profile(args.rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
