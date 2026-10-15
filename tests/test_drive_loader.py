"""Exercise the real Streamlit TTL cache, Secrets parsing and unchanged pipeline."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import streamlit as st

from services.config import Settings
from services.drive import DriveConfig, DriveError, FOLDER_ID, REFERENCE_ID
from services.drive_loader import (CachedDriveClient, _parse_account, _read_configuration,
                                   create_drive_repository)
from services.presentation import format_timestamp
from test_drive import FakeDrive, remote, FILE_A, FILE_B

ACCOUNT = {'type':'service_account', 'client_email':'test@example.iam.gserviceaccount.com',
           'private_key':'TEST_PRIVATE_KEY_PLACEHOLDER', 'private_key_id':'TEST_KEY',
           'token_uri':'https://oauth2.googleapis.com/token'}


def secrets(account=None, folder_id=FOLDER_ID):
    return {'drive': {'folder_id':folder_id, 'reference_file_id':REFERENCE_ID,
                       'service_account_json':ACCOUNT if account is None else account}}


class DriveLoaderTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.temp=tempfile.TemporaryDirectory()
        self.settings=replace(Settings(),app_env='community-drive',local_data_dir=Path(self.temp.name))

    def tearDown(self):
        st.cache_data.clear()
        self.temp.cleanup()

    def test_account_json_string_and_mapping_are_supported(self):
        self.assertEqual(_parse_account(json.dumps(ACCOUNT)),ACCOUNT)
        config,info=_read_configuration(secrets())
        self.assertEqual(info,ACCOUNT)
        self.assertEqual(config.folder_id,FOLDER_ID)
        self.assertEqual(config.refresh_seconds,1800)
        minimal, _ = _read_configuration({'drive':{'folder_id':FOLDER_ID,'service_account_json':json.dumps(ACCOUNT)}})
        self.assertEqual(minimal.reference_file_id,REFERENCE_ID)

    def test_missing_or_malformed_secrets_are_clear_and_redacted(self):
        for config in [{}, {'drive':{}}, secrets(account='PRIVATE_VALUE invalid JSON'),
                       secrets(account=['PRIVATE_VALUE']), secrets(account={'type':'authorized_user'})]:
            with self.subTest(config_type=type(config)), self.assertRaises(DriveError) as caught:
                _read_configuration(config)
            self.assertNotIn('PRIVATE_VALUE',str(caught.exception))

    def test_new_secrets_take_precedence_and_legacy_still_works(self):
        new=secrets();new['gcp_service_account']={'invalid':'ignored'}
        self.assertEqual(_read_configuration(new)[1],ACCOUNT)
        config,info=_read_configuration({'google_drive':{'folder_id':FOLDER_ID},'gcp_service_account':ACCOUNT})
        self.assertEqual(config.refresh_seconds,1800)
        self.assertEqual(info,ACCOUNT)

    def test_factory_reads_service_account_and_keeps_credentials_out_of_metadata(self):
        api=FakeDrive()
        with patch('services.drive_loader.DriveClient.from_service_account',return_value=api) as connect:
            repo=create_drive_repository(self.settings,secrets())
        connect.assert_called_once_with(ACCOUNT,25*1024*1024)
        repo.synchronize()
        pointer=repo.get_active_version()
        report=repo.manager.read_validation(pointer['active_version'])
        serialized=json.dumps(report)
        self.assertNotIn(ACCOUNT['private_key'],serialized)
        self.assertNotIn(ACCOUNT['client_email'],serialized)
        self.assertEqual(report['drive']['source_name'],'Ton_CCDC_13102026.xlsx')
        self.assertTrue(report['drive']['source_modified_at_utc'])
        self.assertEqual(len(repo.read_active_dataset(date(2026,10,15))),30)

    def test_factory_credential_errors_are_sanitized(self):
        with patch('services.drive_loader.DriveClient.from_service_account',side_effect=DriveError('PRIVATE_VALUE')):
            with self.assertRaisesRegex(DriveError,'service_account_json') as caught:
                create_drive_repository(self.settings,secrets())
        self.assertNotIn('PRIVATE_VALUE',str(caught.exception))

    def test_credentials_and_source_changes_use_separate_cache_namespaces(self):
        with patch('services.drive_loader.DriveClient.from_service_account',return_value=FakeDrive()):
            first=create_drive_repository(self.settings,secrets())
            changed=create_drive_repository(self.settings,secrets(account={**ACCOUNT,'private_key_id':'ROTATED_KEY'}))
            other_source=create_drive_repository(self.settings,secrets(folder_id='another_folder_001'))
        self.assertNotEqual(first.client.scope,changed.client.scope)
        self.assertNotEqual(first.client.scope,other_source.client.scope)
        self.assertNotEqual(first.settings.local_data_dir,changed.settings.local_data_dir)
        self.assertNotIn(ACCOUNT['private_key'],first.client.scope)

    def test_cache_warm_read_avoids_download_and_metadata_is_always_fresh(self):
        api=FakeDrive();cached=CachedDriveClient(api,'scope-a')
        file=api.files[0]
        first=cached.download(file);self.assertEqual(cached.download(file),first)
        self.assertEqual(api.downloads,[FILE_A])
        api.change_during_download=True
        self.assertEqual(cached.metadata(FILE_A).version,'changed')

    def test_cache_isolated_between_accounts_and_targeted_refresh(self):
        api_a,api_b=FakeDrive(),FakeDrive()
        a,b=CachedDriveClient(api_a,'scope-a'),CachedDriveClient(api_b,'scope-b')
        a.download(api_a.files[0]);b.download(api_b.files[0])
        a.clear_cache()
        a.download(api_a.files[0]);b.download(api_b.files[0])
        self.assertEqual(len(api_a.downloads),2)
        self.assertEqual(len(api_b.downloads),1)

    def test_real_cache_expiry_is_thirty_minutes(self):
        clock=[1000.0]
        with patch('streamlit.runtime.caching.cache_utils.TTLCACHE_TIMER',lambda:clock[0]):
            st.cache_data.clear()
            api=FakeDrive();cached=CachedDriveClient(api,'ttl-scope')
            cached.download(api.files[0])
            clock[0]+=1799
            cached.download(api.files[0]);self.assertEqual(len(api.downloads),1)
            clock[0]+=2
            cached.download(api.files[0]);self.assertEqual(len(api.downloads),2)

    def test_connection_failures_are_not_cached(self):
        api=Mock();api.download.side_effect=[DriveError('Không kết nối được Drive.'),b'OK']
        cached=CachedDriveClient(api,'error-scope');file=remote(FILE_A,'Ton_CCDC_13102026.xlsx')
        with self.assertRaises(DriveError):cached.download(file)
        self.assertEqual(cached.download(file),b'OK')
        self.assertEqual(api.download.call_count,2)

    def test_refresh_invalidates_index_and_picks_new_snapshot(self):
        api=FakeDrive()
        with patch('services.drive_loader.DriveClient.from_service_account',return_value=api):
            repo=create_drive_repository(self.settings,secrets())
        repo.synchronize()
        from services.demo import synthetic_files
        content=synthetic_files(8)[0]
        api.contents[FILE_B]=content
        api.files.append(remote(FILE_B,'Ton_CCDC_14102026.xlsx',content=content))
        repo.synchronize()
        self.assertEqual(repo.get_active_version()['data_date'],'2026-10-13')
        repo.synchronize(force=True)
        self.assertEqual(repo.get_active_version()['data_date'],'2026-10-14')
        self.assertFalse(repo.last_error)

    def test_timestamp_display_uses_vietnam_time(self):
        self.assertEqual(format_timestamp('2026-10-15T02:00:00Z'),'15/10/2026 09:00')
        self.assertEqual(format_timestamp('not-a-date'),'Chưa có thông tin')


if __name__=='__main__':unittest.main()
