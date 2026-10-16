"""Real Excel pipeline with a fake read-only Drive, plus HTTP boundary checks."""
from dataclasses import replace
from datetime import date
import hashlib
import io
import tempfile
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from openpyxl import load_workbook

from services.config import Settings
from services.demo import synthetic_files
from services.drive import (DriveClient, DriveConfig, DriveError, DriveFile, DriveRepository,
                            FOLDER, FOLDER_ID, REFERENCE_ID, SHEET, XLSX, snapshot_date)

FILE_A = "inventory_file_0001"
FILE_B = "inventory_file_0002"


def remote(file_id, name, mime=XLSX, version="1", content=None):
    return DriveFile(file_id, name, mime, "2026-10-15T02:00:00Z", version,
                     hashlib.md5(content).hexdigest() if content else "", len(content or b""))


class FakeDrive:
    def __init__(self, rows=30):
        inventory, classification = synthetic_files(rows)
        self.files = [remote(FILE_A, "Ton_CCDC_13102026.xlsx", content=inventory)]
        self.reference = remote(REFERENCE_ID, "Phan_loai_VTTB.xlsx", content=classification)
        self.contents = {FILE_A: inventory, REFERENCE_ID: classification}
        self.downloads = []
        self.error = None
        self.change_during_download = False

    def list_inventory(self, config):
        if self.error:
            raise self.error
        return self.files

    def metadata(self, file_id):
        file = self.reference if file_id == REFERENCE_ID else next(f for f in self.files if f.id == file_id)
        return replace(file, version="changed") if self.change_during_download else file

    def download(self, file):
        self.downloads.append(file.id)
        return self.contents[file.id]


class DriveRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = patch('services.drive.vn_today', return_value=date(2026,10,15))
        self.clock.start()
        self.settings = replace(Settings(), app_env="community-drive", local_data_dir=self.temp.name)
        self.client = FakeDrive()
        self.repo = DriveRepository(self.settings, DriveConfig(), self.client)

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def add_file(self, rows=12, name="Ton_CCDC_14102026.xlsx", content=None):
        content = content if content is not None else synthetic_files(rows)[0]
        self.client.contents[FILE_B] = content
        self.client.files.append(remote(FILE_B, name, content=content))

    def test_initial_sync_date_mapping_and_raw_source(self):
        self.repo.synchronize()
        active = self.repo.get_active_version()
        self.assertEqual(active['data_date'], '2026-10-13')
        frame = self.repo.read_active_dataset(date(2026,10,15))
        self.assertEqual(len(frame), 30)
        self.assertTrue(frame['Loại VTTB'].ne('Chưa phân loại').any())
        self.assertEqual(self.repo.manager.read_source(active['active_version']), self.client.contents[FILE_A])
        self.assertEqual(self.repo.manager.read_classification_source(active['active_version']), self.client.contents[REFERENCE_ID])
        report = self.repo.manager.read_validation(active['active_version'])
        self.assertEqual(report['drive']['date_format'], 'dd/mm/yyyy')
        self.assertFalse(self.repo.last_error)

    def test_unchanged_files_do_not_download_parse_or_publish_again(self):
        self.repo.synchronize()
        active = self.repo.get_active_version()
        with patch('services.drive.transform_inventory', side_effect=AssertionError('Repeated parse')):
            self.repo.synchronize(force=True)
        self.assertEqual(self.client.downloads, [REFERENCE_ID, FILE_A])
        self.assertEqual(self.repo.get_active_version(), active)

    def test_five_concurrent_refreshes_share_one_validated_snapshot(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures=[pool.submit(self.repo.synchronize) for _ in range(5)]
            for future in futures:future.result(timeout=30)
        self.assertEqual(self.client.downloads,[REFERENCE_ID,FILE_A])
        self.assertEqual(len([v for v in self.repo.list_versions() if v['current']]),1)
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))),30)

    def test_new_snapshot_replaces_rows_instead_of_adding_them(self):
        self.repo.synchronize()
        self.add_file(rows=12)
        self.repo.synchronize(force=True)
        self.assertEqual(self.repo.get_active_version()['data_date'], '2026-10-14')
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))), 12)

    def test_latest_uses_filename_date_before_modified_time(self):
        self.add_file()
        self.client.files[0] = replace(self.client.files[0], modified_time='2026-10-15T23:59:59Z')
        self.repo.synchronize()
        self.assertEqual(self.repo.get_active_version()['data_date'], '2026-10-14')

    def test_future_snapshot_not_activated(self):
        self.add_file(name='Ton_CCDC_16102026.xlsx')
        self.repo.synchronize()
        self.assertEqual(self.repo.get_active_version()['data_date'], '2026-10-13')

    def test_historical_date_reads_drive_file_without_changing_active(self):
        self.add_file()
        self.repo.synchronize()
        active = self.repo.get_active_version()
        older = self.repo.select_version('2026-10-13')
        self.assertEqual(older['cutoff'], '2026-10-13')
        self.assertEqual(len(self.repo.read_dataset(older['id'], date(2026,10,15))),30)
        self.assertEqual(self.repo.get_active_version(), active)
        count = len(self.client.downloads)
        self.assertEqual(self.repo.select_version('2026-10-13'), older)
        self.assertEqual(len(self.client.downloads), count)
        self.assertIsNone(self.repo.select_version('2026-10-12'))

    def test_invalid_new_excel_keeps_active_and_does_not_use_demo(self):
        self.repo.synchronize()
        active = self.repo.get_active_version()
        self.add_file(content=b'not an xlsx')
        self.repo.synchronize(force=True)
        self.assertEqual(self.repo.get_active_version(),active)
        self.assertTrue(self.repo.last_error)
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))),30)

    def test_lost_drive_access_keeps_last_good_dataset_and_reports_error(self):
        self.repo.synchronize()
        active = self.repo.get_active_version()
        self.client.error = DriveError('Không có quyền đọc Drive.')
        self.repo.synchronize(force=True)
        self.assertEqual(self.repo.get_active_version(),active)
        self.assertIn('quyền', self.repo.last_error)

    def test_first_sync_failure_is_explicit_not_synthetic_fallback(self):
        self.client.error = DriveError('Không có quyền đọc Drive.')
        self.repo.synchronize()
        self.assertIn('quyền đọc Drive', self.repo.last_error)
        self.repo.synchronize()  # A throttled repeat must retain the same error.
        self.assertIn('quyền đọc Drive', self.repo.last_error)
        self.assertIsNone(self.repo.get_active_version())
        self.repo.seed_synthetic_demo()
        self.assertIsNone(self.repo.get_active_version())

    def test_revision_changed_during_download_does_not_publish(self):
        self.client.change_during_download = True
        self.repo.synchronize()
        self.assertIn('đang được cập nhật', self.repo.last_error)
        self.assertIsNone(self.repo.get_active_version())

    def test_reference_revision_change_reprocesses_pair(self):
        self.repo.synchronize()
        previous = self.repo.get_active_version()
        self.client.reference = replace(self.client.reference, version='2')
        self.repo.synchronize(force=True)
        self.assertNotEqual(self.repo.get_active_version()['active_version'], previous['active_version'])
        self.assertEqual(len(self.client.downloads),4)

    def test_successful_retry_clears_warning(self):
        self.repo.synchronize()
        self.client.error=DriveError('Không có quyền đọc Drive.')
        self.repo.synchronize(force=True)
        self.client.error=None
        self.repo.synchronize(force=True)
        self.assertFalse(self.repo.last_error)

    def test_cold_start_recovers_previous_valid_drive_file_without_ingestion(self):
        self.add_file(content=b'not an xlsx')
        self.repo.synchronize()
        self.assertEqual(self.repo.get_active_version()['data_date'], '2026-10-13')
        self.assertIn('Ton_CCDC_14102026.xlsx', self.repo.last_error)
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))), 30)
        with tempfile.TemporaryDirectory() as directory:
            restarted = DriveRepository(replace(self.settings, local_data_dir=directory), DriveConfig(), self.client)
            self.assertIsNone(restarted.get_active_version())
            restarted.synchronize()
            self.assertEqual(restarted.get_active_version()['data_date'], '2026-10-13')
            self.assertTrue(restarted.last_error)

    def test_no_valid_file_keeps_empty_state_and_does_not_repeat_excel_validation(self):
        self.client.contents[FILE_A] = b'invalid xlsx'
        self.repo.synchronize()
        self.assertIsNone(self.repo.get_active_version())
        self.assertIn('Ton_CCDC_13102026.xlsx', self.repo.last_error)
        with patch('services.drive.transform_inventory', side_effect=AssertionError('reparsed within TTL')):
            self.repo.synchronize()
        self.assertIsNone(self.repo.get_active_version())
        self.assertEqual(len(self.repo.last_failures), 1)
        self.client.contents[FILE_A] = synthetic_files(8)[0]
        self.repo.synchronize(force=True)
        self.assertFalse(self.repo.last_error)
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))), 8)

    def test_reference_cell_formula_reports_actual_file_sheet_cell_and_stops_recovery(self):
        workbook = load_workbook(io.BytesIO(self.client.contents[REFERENCE_ID]))
        workbook.active['B2'] = '=1+1'
        output = io.BytesIO(); workbook.save(output); workbook.close()
        self.client.contents[REFERENCE_ID] = output.getvalue()
        self.add_file()
        self.repo.synchronize()
        self.assertIsNone(self.repo.get_active_version())
        self.assertIn('Phan_loai_VTTB.xlsx', self.repo.last_error)
        self.assertIn('B2', self.repo.last_error)
        self.assertEqual(self.repo.last_failures[0]['source_role'], 'reference')
        self.assertEqual(self.client.downloads, [REFERENCE_ID])

    def test_cold_fallback_does_not_skip_connection_errors(self):
        self.add_file()
        with patch.object(self.client, 'download', side_effect=DriveError('Mất kết nối.')):
            self.repo.synchronize()
        self.assertIsNone(self.repo.get_active_version())
        self.assertIn('Mất kết nối', self.repo.last_error)

    def test_pipeline_logs_stages_without_cell_content_or_credentials(self):
        with self.assertLogs('services', level='INFO') as captured:
            self.repo.synchronize()
        output = '\n'.join(captured.output)
        for stage in ['drive_sync_started', 'drive_inventory_listed', 'drive_download_completed',
                      'excel_validation_succeeded', 'drive_processing_completed', 'drive_cache_activated', 'drive_sync_completed']:
            self.assertIn(stage, output)
        self.assertNotIn('private_key', output)

    def test_drive_path_never_uses_legacy_publish_or_staging(self):
        with patch('services.versions.VersionManager.commit', side_effect=AssertionError('legacy publish called')), \
                patch('services.versions.VersionManager.stage_upload', side_effect=AssertionError('legacy staging called')):
            self.repo.synchronize()
        self.assertIsNotNone(self.repo.get_active_version())
        keys = self.repo.manager.storage.list('')
        self.assertTrue(all(key.startswith('drive-cache/') for key in keys))
        self.assertNotIn('metadata/active.json', keys)

    def test_partial_materialization_failure_keeps_exact_previous_snapshot(self):
        self.repo.synchronize()
        active = self.repo.get_active_version()
        self.add_file()
        put = self.repo.manager.storage.put
        def fail_dataset(key, *args, **kwargs):
            if key.endswith('/dataset.parquet'):
                raise OSError('disk unavailable')
            return put(key, *args, **kwargs)
        with patch.object(self.repo.manager.storage, 'put', side_effect=fail_dataset):
            self.repo.synchronize(force=True)
        self.assertEqual(self.repo.get_active_version(), active)
        self.assertEqual(len(self.repo.read_active_dataset(date(2026,10,15))), 30)
        self.assertTrue(self.repo.last_error)


class DriveNameTests(unittest.TestCase):
    def test_exact_ddmmyyyy_names_and_invalid_calendar_dates(self):
        self.assertEqual(snapshot_date('Ton_CCDC_01022026.xlsx'),date(2026,2,1))
        self.assertEqual(snapshot_date('ton_ccdc_29022024'),date(2024,2,29))
        self.assertIsNone(snapshot_date('~$Ton_CCDC_01022026.xlsx'))
        self.assertIsNone(snapshot_date('Ton_CCDC_1022026.xlsx'))
        self.assertIsNone(snapshot_date('other.xlsx'))
        for value in ['Ton_CCDC_29022025.xlsx','Ton_CCDC_31042026.xlsx']:
            with self.assertRaises(DriveError): snapshot_date(value)

    def test_config_rejects_bad_ids_and_limits(self):
        for values in [{'folder_id': "id' or true"}, {'recursive': 'true'}, {'refresh_seconds':1}, {'refresh_seconds':True}, {'unknown':1}]:
            with self.subTest(values=values), self.assertRaises((DriveError, TypeError)):
                DriveConfig.from_mapping(values)


class Response:
    def __init__(self, data=None, status=200, chunks=()):
        self.data, self.status_code, self.chunks = data, status, chunks
        self.closed = False
    def json(self): return self.data
    def iter_content(self, size): return iter(self.chunks)
    def close(self): self.closed = True


def metadata_json(file_id, name, mime=XLSX):
    return {'id':file_id,'name':name,'mimeType':mime,'version':'1','modifiedTime':'2026-10-15T00:00:00Z'}


class DriveHTTPTests(unittest.TestCase):
    def test_wrong_names_invalid_dates_and_temporary_files_are_not_candidates(self):
        client=DriveClient(Mock())
        files=[remote(FILE_A,'Ton_CCDC_13102026.xlsx'),
               remote('invalid_file_01','Ton_CCDC_31022026.xlsx'),
               remote('temporary_file1','~$Ton_CCDC_14102026.xlsx'),
               remote('other_file_0001','Other_15102026.xlsx')]
        with patch.object(client,'metadata',return_value=remote(FOLDER_ID,'Data',FOLDER)), \
                patch.object(client,'_children',return_value=files):
            self.assertEqual([file.id for file in client.list_inventory(DriveConfig())],[FILE_A])

    def test_parent_data_subfolder_pagination_and_reference_exclusion(self):
        session=Mock()
        def get(url, params, **kwargs):
            if url.endswith('/'+FOLDER_ID): return Response(metadata_json(FOLDER_ID,'CCDC',FOLDER))
            if "'"+FOLDER_ID+"'" in params.get('q',''):
                return Response({'files':[metadata_json('data_folder_001','Data',FOLDER)]})
            if not params.get('pageToken'):
                return Response({'files':[metadata_json(FILE_A,'Ton_CCDC_13102026.xlsx')],'nextPageToken':'page2'})
            return Response({'files':[metadata_json(REFERENCE_ID,'Phan_loai_VTTB.xlsx')]})
        session.get.side_effect=get
        files=DriveClient(session).list_inventory(DriveConfig())
        self.assertEqual([file.id for file in files],[FILE_A])
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in session.get.call_args_list))

    def test_direct_data_folder_and_recursive_child(self):
        session=Mock()
        def get(url, params, **kwargs):
            if url.endswith('/'+FOLDER_ID): return Response(metadata_json(FOLDER_ID,'Data',FOLDER))
            if "'"+FOLDER_ID+"'" in params.get('q',''):
                return Response({'files':[metadata_json('subfolder_0001','2026',FOLDER)]})
            return Response({'files':[metadata_json(FILE_A,'Ton_CCDC_13102026.xlsx')]})
        session.get.side_effect=get
        self.assertEqual(len(DriveClient(session).list_inventory(DriveConfig())),1)

    def test_incomplete_search_and_repeating_page_are_rejected(self):
        for value in [{'files':[], 'incompleteSearch':True}, {'files':[],'nextPageToken':'same'}]:
            session=Mock();session.get.return_value=Response(value)
            with self.assertRaises(DriveError):DriveClient(session)._children(FOLDER_ID)

    def test_recursive_folder_limit_is_checked_before_fetching_children(self):
        client=DriveClient(Mock())
        children=[remote(f'subfolder_{i:04d}',str(i),FOLDER) for i in range(200)]
        with patch.object(client,'metadata',return_value=remote(FOLDER_ID,'Data',FOLDER)), \
             patch.object(client,'_children',side_effect=lambda file_id:children if file_id==FOLDER_ID else []) as fetch:
            with self.assertRaisesRegex(DriveError,'100 thư mục'):client.list_inventory(DriveConfig())
            self.assertLessEqual(fetch.call_count,100)

    def test_native_export_size_or_permission_error_has_actionable_message(self):
        session=Mock();session.get.return_value=Response(status=403)
        with self.assertRaisesRegex(DriveError,'giới hạn export'):
            DriveClient(session).download(remote(FILE_A,'Ton_CCDC_13102026',SHEET))

    def test_native_sheet_export_and_xlsx_media_are_distinct(self):
        for mime, suffix, params in [(SHEET,'/export',{'mimeType':XLSX}), (XLSX,'/'+FILE_A,{'alt':'media','supportsAllDrives':'true'})]:
            session=Mock();response=Response(chunks=[b'PK123']);session.get.return_value=response
            self.assertEqual(DriveClient(session).download(remote(FILE_A,'Ton_CCDC_13102026',mime)),b'PK123')
            self.assertTrue(session.get.call_args.args[0].endswith(suffix))
            self.assertEqual(session.get.call_args.kwargs['params'],params)
            self.assertTrue(response.closed)

    def test_download_limits_and_checksum_are_enforced(self):
        file=remote(FILE_A,'Ton_CCDC_13102026.xlsx')
        for changed, chunks in [(replace(file,size=50),[b'x']), (file,[b'12345',b'67890']), (replace(file,checksum='wrong'),[b'123'])]:
            session=Mock();session.get.return_value=Response(chunks=chunks)
            with self.assertRaises(DriveError): DriveClient(session,max_bytes=8).download(changed)

    def test_api_auth_errors_do_not_expose_response_or_token(self):
        for status in [401,403,404,429,500]:
            session=Mock();response=Response({'error':'PRIVATE_TOKEN'},status);session.get.return_value=response
            with self.assertRaises(DriveError) as caught:DriveClient(session).metadata(FILE_A)
            self.assertNotIn('PRIVATE_TOKEN', str(caught.exception))
            self.assertTrue(response.closed)
        session=Mock();session.get.side_effect=RuntimeError('PRIVATE_TOKEN')
        with self.assertRaises(DriveError) as caught:DriveClient(session).metadata(FILE_A)
        self.assertNotIn('PRIVATE_TOKEN',str(caught.exception))

    def test_missing_credentials_are_actionable_and_sanitized(self):
        with patch('google.auth.default',side_effect=RuntimeError('PRIVATE_KEY')):
            with self.assertRaisesRegex(DriveError,'Streamlit Secrets') as caught:DriveClient.from_service_account()
        self.assertNotIn('PRIVATE_KEY',str(caught.exception))


if __name__ == '__main__':
    unittest.main()
