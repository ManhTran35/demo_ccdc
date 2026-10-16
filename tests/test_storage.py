import concurrent.futures
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest

from services.storage import (GCSStorageAdapter, LocalStorageAdapter, ObjectData,
                              ObjectNotFound, PreconditionFailed, StorageError)


class FakeNotFound(Exception):
    code = 404


class FakeConflict(Exception):
    code = 412


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name, self.generation = bucket, name, None

    def reload(self):
        if self.name not in self.bucket.objects:
            raise FakeNotFound()
        self.generation = self.bucket.objects[self.name][1]

    def download_as_bytes(self, **kwargs):
        self.bucket.calls.append(("get", self.name, kwargs))
        if self.name not in self.bucket.objects:
            raise FakeNotFound()
        data, generation = self.bucket.objects[self.name]
        if kwargs["if_generation_match"] != generation:
            raise FakeConflict()
        return data

    def upload_from_string(self, data, **kwargs):
        self.bucket.calls.append(("put", self.name, kwargs))
        with self.bucket.lock:
            previous = self.bucket.objects.get(self.name)
            current = previous[1] if previous else 0
            expected = kwargs.get("if_generation_match")
            if expected is not None and expected != current:
                raise FakeConflict()
            self.generation = current + 1
            self.bucket.objects[self.name] = data, self.generation


class FakeBucket:
    def __init__(self):
        self.objects, self.calls, self.lock = {}, [], threading.Lock()

    def blob(self, name):
        return FakeBlob(self, name)


class FakeGCSClient:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        return self.buckets.setdefault(name, FakeBucket())

    def list_blobs(self, bucket, prefix):
        return [bucket.blob(name) for name in bucket.objects if name.startswith(prefix)]


def local_process_cas(args):
    root, expected, payload = args
    adapter = LocalStorageAdapter(root)
    try:
        return adapter.put("metadata/active.json", payload, if_generation_match=expected)
    except PreconditionFailed:
        return None


class StoreContract:
    def test_missing_and_round_trip(self):
        with self.assertRaises(ObjectNotFound):
            self.store.get("a/one")
        self.assertEqual(self.store.put("a/one", b"hello", if_generation_match=0), 1)
        self.assertEqual(self.store.get("a/one"), ObjectData(b"hello", 1))
        self.assertEqual(self.store.put("a/one", b"next", if_generation_match=1), 2)
        self.assertEqual(self.store.get("a/one"), ObjectData(b"next", 2))

    def test_create_only_and_stale_write_leave_bytes_unchanged(self):
        generation = self.store.put("a/one", b"original", if_generation_match=0)
        for stale in (0, generation + 4):
            with self.subTest(generation=stale), self.assertRaises(PreconditionFailed):
                self.store.put("a/one", b"bad", if_generation_match=stale)
            self.assertEqual(self.store.get("a/one").data, b"original")

    def test_list_has_literal_prefix(self):
        for key in ("test_a/1", "test%/2", "test_a/3", "other/4"):
            self.store.put(key, b"a", if_generation_match=0)
        self.assertEqual(self.store.list("test_a/"), ["test_a/1", "test_a/3"])
        self.assertEqual(self.store.list("test%/"), ["test%/2"])

    def test_unsafe_names_rejected_on_all_operations(self):
        for key in ("", "/abs", "../x", "x/../y", "x/./y", "x//y", "x\\y", "x\x00y", "x/"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.store.get(key)
                with self.assertRaises(ValueError):
                    self.store.put(key, b"a")
        for prefix in ("/", "../", "x//", "x\\y"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.store.list(prefix)

    def test_generation_and_payload_types(self):
        for value in (-1, True, "1", 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.put("one", b"a", if_generation_match=value)
        with self.assertRaises(TypeError):
            self.store.put("one", "text")

    def test_concurrent_cas_has_one_winner(self):
        generation = self.store.put("active", b"first", if_generation_match=0)
        barrier = threading.Barrier(2)
        def write(payload):
            barrier.wait()
            try:
                return self.store.put("active", payload, if_generation_match=generation)
            except PreconditionFailed:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (b"winner-a", b"winner-b")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertIn(self.store.get("active").data, (b"winner-a", b"winner-b"))
        self.assertEqual(self.store.get("active").generation, generation + 1)


class LocalStorageTests(StoreContract, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStorageAdapter(self.temp.name)

    def test_reopen_and_cross_process_atomicity(self):
        generation = self.store.put("metadata/active.json", b"initial", if_generation_match=0)
        self.assertEqual(LocalStorageAdapter(self.temp.name).get("metadata/active.json").data, b"initial")
        context = multiprocessing.get_context("spawn")
        with context.Pool(2) as pool:
            results = pool.map(local_process_cas, [(self.temp.name, generation, b"a"),
                                                   (self.temp.name, generation, b"b")])
        self.assertEqual(sum(result is not None for result in results), 1)
        obj = self.store.get("metadata/active.json")
        self.assertEqual(obj.generation, 2)
        self.assertIn(obj.data, (b"a", b"b"))


class GCSStorageTests(StoreContract, unittest.TestCase):
    def setUp(self):
        self.client = FakeGCSClient()
        self.store = GCSStorageAdapter("private-bucket", prefix="staging/app", client=self.client)

    def test_prefix_isolated_and_checksum_enabled(self):
        other = GCSStorageAdapter("private-bucket", prefix="staging/app-other", client=self.client)
        other.put("a", b"other", if_generation_match=0)
        self.store.put("a", b"own", if_generation_match=0)
        self.assertEqual(self.store.list(), ["a"])
        self.assertEqual(self.store.get("a").data, b"own")
        calls = self.client.bucket("private-bucket").calls
        self.assertTrue(all(kwargs.get("checksum") == "auto" for _, _, kwargs in calls))
        self.assertTrue(all("if_generation_match" in kwargs for _, _, kwargs in calls))

    def test_read_bytes_and_generation_bound_during_race(self):
        bucket = self.client.bucket("private-bucket")
        self.store.put("race", b"before", if_generation_match=0)
        normal_blob = bucket.blob
        def racing_blob(name):
            blob = normal_blob(name)
            original_reload = blob.reload
            def reload():
                original_reload()
                bucket.objects[name] = (b"after", 2)
            blob.reload = reload
            return blob
        bucket.blob = racing_blob
        with self.assertRaises(PreconditionFailed):
            self.store.get("race")

    def test_backend_failure_translated_without_response_body(self):
        bucket = self.client.bucket("private-bucket")
        def broken(name):
            blob = FakeBlob(bucket, name)
            def reload():
                raise RuntimeError("private backend body")
            blob.reload = reload
            return blob
        bucket.blob = broken
        with self.assertRaisesRegex(StorageError, "^GCS object operation failed$"):
            self.store.get("a")

    def test_unsafe_prefix_rejected_and_large_full_name_rejected(self):
        for prefix in ("/global", "../global", "x//y", "x\\y"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                GCSStorageAdapter("bucket", prefix=prefix, client=self.client)
        store = GCSStorageAdapter("bucket", prefix="p" * 1000, client=self.client)
        with self.assertRaises(ValueError):
            store.put("k" * 30, b"a")


if __name__ == "__main__":
    unittest.main()
