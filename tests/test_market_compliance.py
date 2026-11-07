# -*- coding: utf-8 -*-
"""市场上架规范守卫：日志来源、持久化位置、插件元数据与打包精简。

对应 AstrBot Cloud 插件市场审核的两条硬性规则：

1. 日志只能来自 ``astrbot.api``，禁止使用 Python 内置 ``logging``；
2. 缓存 / 状态 / 日志必须写入 ``data/plugin_data/<plugin_name>``，不得写入插件安装目录。

本测试不依赖 AstrBot 框架，CI 与本地 ``python -X utf8 scripts/check.py`` 都会执行。
"""
import ast
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
METADATA_KEYS = ("name", "display_name", "desc", "short_desc", "author", "version", "repo")


def plugin_sources():
    for path in sorted(PLUGINS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def read_metadata(path):
    """极简 metadata.yaml 解析：只取顶层 ``key: value`` 字符串，避免引入 YAML 依赖。"""
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or line.startswith((" ", "\t", "-", "---")):
            continue
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip()
    return data


class LoggingComplianceTests(unittest.TestCase):
    def test_plugins_never_use_stdlib_logging(self):
        offenders = []
        for path in plugin_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "logging" or alias.name.startswith("logging."):
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "logging" or module.startswith("logging."):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: from {module} import ...")
                elif (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "logging"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: logging.{node.attr}")
        self.assertEqual([], offenders, "插件内禁止使用 Python 内置 logging，请改用 from astrbot.api import logger")


class PersistenceComplianceTests(unittest.TestCase):
    def test_no_cache_directory_beside_source_files(self):
        """禁止 ``<插件目录>/cache`` 这类相对源码文件的写入路径。"""
        offenders = []
        for path in plugin_sources():
            if path.name == "paths.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "__file__" in line and '"cache"' in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual([], offenders, "缓存目录必须经 astrbot_plugin_hd2_common.paths 解析到 data/plugin_data")

    def test_common_paths_helper_prefers_framework_api(self):
        text = (PLUGINS / "astrbot_plugin_hd2_common" / "paths.py").read_text(encoding="utf-8")
        self.assertIn("data/plugin_data", text)
        self.assertIn("StarTools.get_data_dir", text)

    def test_gitignore_ignores_plugin_data(self):
        self.assertIn("data/plugin_data/", (ROOT / ".gitignore").read_text(encoding="utf-8"))


class MarketPackagingTests(unittest.TestCase):
    def plugin_dirs(self):
        return [path for path in PLUGINS.iterdir() if path.is_dir() and (path / "main.py").is_file()]

    def test_metadata_is_complete_and_versioned(self):
        problems = []
        for directory in self.plugin_dirs():
            metadata_path = directory / "metadata.yaml"
            if not metadata_path.is_file():
                problems.append(f"{directory.name}: 缺少 metadata.yaml")
                continue
            metadata = read_metadata(metadata_path)
            missing = [key for key in METADATA_KEYS if not metadata.get(key)]
            if missing:
                problems.append(f"{directory.name}: 缺少字段 {missing}")
            if metadata.get("name") != directory.name:
                problems.append(f"{directory.name}: name 与目录名不一致 ({metadata.get('name')})")
            if not VERSION_RE.match(metadata.get("version", "")):
                problems.append(f"{directory.name}: version 不是 x.y.z ({metadata.get('version')})")
        self.assertEqual([], problems)

    def test_market_archive_excludes_dev_only_paths(self):
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for entry in ("/scripts/", "/tests/", "/docs/", "/data/", "/temp/", "/.github/"):
            self.assertIn(f"{entry} export-ignore", text, f"市场安装包未排除 {entry}")

    def test_dev_scripts_inside_plugin_dirs_are_export_ignored(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        offenders = []
        for directory in self.plugin_dirs():
            for path in directory.glob("_*.py"):
                relative = "/" + str(path.relative_to(ROOT)).replace(os.sep, "/")
                if f"{relative} export-ignore" not in attributes:
                    offenders.append(relative)
        self.assertEqual([], offenders, "插件目录内的一次性开发脚本必须从市场安装包中排除")


if __name__ == "__main__":
    unittest.main()
