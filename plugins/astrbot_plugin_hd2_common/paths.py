"""插件数据目录：所有可写缓存/状态统一落在 ``data/plugin_data/<plugin_name>``。

AstrBot 规范要求插件把运行期产生的缓存、状态与日志写到数据目录，而不是插件安装目录
（安装目录随市场更新会被整体替换，也会影响用户数据迁移与审计）。

解析顺序：

1. 环境变量 ``HD2_PLUGIN_DATA_DIR``（离线工具/测试可显式指定根目录）；
2. AstrBot 官方接口 ``StarTools.get_data_dir(plugin_name)``（= ``data/plugin_data/<name>``）；
3. 无框架时按本文件位置推导：``<data>/plugins/<name>/paths.py`` → ``<data>/plugin_data``；
   仓库开发布局下则用 ``<repo>/data/plugin_data``（该 ``data`` 目录就是本地 AstrBot 数据目录）。

本模块不导入任何 AstrBot 模块（延迟到调用时），因此离线脚本与单元测试也能直接使用。
"""
import os
import tempfile
from pathlib import Path

_RESOLVED = {}


def _framework_dir(plugin_name):
    """Return AstrBot's own plugin data directory, or ``None`` when unavailable."""
    try:
        from astrbot.api.star import StarTools

        return Path(StarTools.get_data_dir(plugin_name))
    except Exception:  # 框架缺失/未初始化/目录不可写时一律退回本地推导
        return None


def data_root():
    """Return the ``plugin_data`` root directory (no side effects)."""
    override = os.environ.get("HD2_PLUGIN_DATA_DIR")
    if override:
        return Path(override)
    anchor = os.environ.get("ASTRBOT_ROOT")
    if anchor:
        return Path(anchor) / "data" / "plugin_data"
    base = Path(__file__).resolve().parents[2]
    if base.name == "data":
        # 正常安装布局：<AstrBot>/data/plugins/<plugin>/paths.py
        return base / "plugin_data"
    if (base / "data").is_dir():
        # 仓库开发布局：<repo>/data 即本地 AstrBot 数据目录
        return base / "data" / "plugin_data"
    return base / "plugin_data"


def plugin_data_dir(plugin_name, *parts, create=True):
    """Absolute path of ``data/plugin_data/<plugin_name>`` (plus ``parts``).

    Args:
        plugin_name: Plugin package name, e.g. ``astrbot_plugin_hd2_wiki``.
        parts: Optional sub-directories (``"cache"``, ``"logs"`` ...).
        create: Create the directory (default) so callers can write immediately.

    Returns:
        ``pathlib.Path`` guaranteed to exist when ``create`` is true.

    Raises:
        ValueError: If ``plugin_name`` is empty.
    """
    name = str(plugin_name or "").strip()
    if not name:
        raise ValueError("plugin_name 不能为空")
    key = (name,)
    base = _RESOLVED.get(key)
    if base is None:
        override = os.environ.get("HD2_PLUGIN_DATA_DIR")
        if override:
            base = Path(override) / name
        else:
            base = _framework_dir(name)
        if base is None:
            base = data_root() / name
        _RESOLVED[key] = base
    path = Path(base).joinpath(*parts) if parts else Path(base)
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 数据目录不可写时退到系统临时目录，保证插件主流程不因缓存而失败。
            fallback = Path(tempfile.gettempdir()) / "astrbot_plugin_data" / name
            path = fallback.joinpath(*parts) if parts else fallback
            path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_cache_dir(plugin_name, *parts, create=False):
    """``data/plugin_data/<plugin_name>/cache`` (cached artefacts)."""
    return plugin_data_dir(plugin_name, "cache", *parts, create=create)


def plugin_log_dir(plugin_name, create=True):
    """``data/plugin_data/<plugin_name>/logs``."""
    return plugin_data_dir(plugin_name, "logs", create=create)
