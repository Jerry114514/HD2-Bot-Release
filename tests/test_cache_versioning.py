# -*- coding: utf-8 -*-
"""缓存版本与失效测试：ver 往返、TTL 过期回退、站点 data.json 断网用缓存"""
import os
import json
import tempfile
import time
import unittest

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds


class TestCacheRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hd2_ds_cache_")
        self._orig_root = ds._PLUGIN_CACHE_ROOT
        ds.set_cache_root(self.tmp)

    def tearDown(self):
        ds.set_cache_root(self._orig_root)

    def test_roundtrip_keeps_ver(self):
        ds._write_cache("t_cache.json", {"a": 1}, ver="2026-10-24T00:00:00+08:00")
        data, ver, ts = ds._read_cache("t_cache.json")
        self.assertEqual(data, {"a": 1})
        self.assertEqual(ver, "2026-10-24T00:00:00+08:00")
        self.assertGreater(ts, 0)

    def test_missing_cache(self):
        data, ver, ts = ds._read_cache("nope_cache.json")
        self.assertIsNone(data)
        self.assertIsNone(ver)
        self.assertEqual(ts, 0)

    def test_corrupt_cache_is_safe(self):
        p = ds._cache_file("bad_cache.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        data, ver, ts = ds._read_cache("bad_cache.json")
        self.assertIsNone(data)

    def test_ver_changes_detected(self):
        """数据版本（fetchedAt）变化时，缓存 ver 能反映新旧——旧响应不被永久钉死"""
        ds._write_cache("v_cache.json", {"n": 1}, ver="2026-10-23T00:00:00+08:00")
        _, ver1, _ = ds._read_cache("v_cache.json")
        ds._write_cache("v_cache.json", {"n": 2}, ver="2026-10-24T00:00:00+08:00")
        data2, ver2, _ = ds._read_cache("v_cache.json")
        self.assertNotEqual(ver1, ver2)
        self.assertEqual(data2, {"n": 2})


class TestSiteDocFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hd2_ds_site_")
        self._orig_root = ds._PLUGIN_CACHE_ROOT
        ds.set_cache_root(self.tmp)
        self._orig_fetch = ds.fetch_json
        self._orig_mem = dict(ds._SITE_DOC_MEM)

    def tearDown(self):
        ds.fetch_json = self._orig_fetch
        ds.set_cache_root(self._orig_root)
        ds._SITE_DOC_MEM.clear()
        ds._SITE_DOC_MEM.update(self._orig_mem)

    def test_site_doc_serves_cache_when_offline(self):
        cached = {"fetchedAt": "2026-10-23T00:00:00+08:00", "planets": []}
        ds._write_cache("site_doc_cache.json", cached, ver=cached["fetchedAt"])
        ds._SITE_DOC_MEM.update({"ts": 0.0, "doc": None})

        def _boom(url, timeout=20, extra_headers=None):
            raise OSError("offline")
        ds.fetch_json = _boom
        doc = ds.site_doc(force=True)
        self.assertEqual(doc.get("fetchedAt"), cached["fetchedAt"], "断网时必须回退到带版本号的磁盘缓存")

    def test_site_doc_empty_when_never_fetched(self):
        def _boom(url, timeout=20, extra_headers=None):
            raise OSError("offline")
        ds.fetch_json = _boom
        ds._SITE_DOC_MEM.update({"ts": 0.0, "doc": None})
        self.assertEqual(ds.site_doc(force=True), {})

    def test_site_doc_uses_network_when_available(self):
        fresh = {"fetchedAt": "2026-10-24T10:00:00+08:00", "planets": [{"index": 0}]}
        ds.fetch_json = lambda url, timeout=20, extra_headers=None: fresh
        doc = ds.site_doc(force=True)
        self.assertEqual(doc.get("fetchedAt"), fresh["fetchedAt"])
        # 且写入了磁盘缓存（下次断网可用）
        data, ver, _ts = ds._read_cache("site_doc_cache.json")
        self.assertEqual(ver, fresh["fetchedAt"])


class TestRecentFreshnessGuard(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = ds.fetch_json

    def tearDown(self):
        ds.fetch_json = self._orig_fetch
        ds._RECENT_MEM.update({"ts": 0.0, "index": None})

    def test_stale_index_disabled(self):
        """帧清单生成时间超过 3 小时 = 断更：返回 None，整块实测停用（不得用旧帧冒充实测）"""
        old = {"generatedAt": "2026-10-20T00:00:00+08:00", "windowHours": 1, "planets": [7]}
        ds.fetch_json = lambda url, timeout=10: old
        ds._RECENT_MEM.update({"ts": 0.0, "index": None})
        self.assertIsNone(ds.recent_index(force=True))

    def test_fresh_index_ok(self):
        fresh = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                 "windowHours": 1, "planets": [7]}
        ds.fetch_json = lambda url, timeout=10: fresh
        ds._RECENT_MEM.update({"ts": 0.0, "index": None})
        self.assertIsNotNone(ds.recent_index(force=True))

    def test_planet_frames_skip_nonindexed_planet(self):
        fresh = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                 "windowHours": 1, "planets": [7]}
        ds.fetch_json = lambda url, timeout=10: fresh
        ds._RECENT_MEM.update({"ts": 0.0, "index": None})
        self.assertEqual(ds.recent_planet_frames(999), [])


if __name__ == "__main__":
    unittest.main()
