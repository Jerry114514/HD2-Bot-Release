# -*- coding: utf-8 -*-
"""跨平台 CJK 字体解析器（astrbot_plugin_hd2_common）

历史问题：战报卡 / 配装卡 / 样图渲染器把字体路径硬编码成
C:\\Windows\\Fonts\\msyh.ttc，Linux 下必然失败。

解析优先级（两级结果：常规 + 加粗）：
  1. 环境变量 HD2_FONT / HD2_FONT_BOLD（显式指定，立刻生效）
  2. 各操作系统常见 CJK 字体候选清单（按优先级取第一组全部存在的）
  3. Linux：遍历字体目录按文件名特征匹配 + fc-match(fontconfig) 动态发现
  4. 兜底：返回首个候选路径（PIL 抛错时由调用方回退文本输出）

入口：
  resolve_font(bold=False) -> str      单个字体文件路径
  resolve_font_pair()      -> (str, str)  (常规, 加粗)
结果按进程缓存；所有系统调用失败静默降级，绝不抛异常。
"""
import os
import re
import subprocess

# ── 环境变量名 ──
ENV_REG = "HD2_FONT"
ENV_BOLD = "HD2_FONT_BOLD"

# ── Windows：微软雅黑优先，黑体/宋体/Arial 兜底 ──
_WIN_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\msyhl.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    (r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simsun.ttc"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
]

# ── Linux：常见发行版 CJK 字体的精确路径（Debian/Ubuntu/Fedora/Arch） ──
_LINUX_CANDIDATES = [
    # 文泉驿（apt install fonts-wqy-microhei / fonts-wqy-zenhei）
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",) * 2,
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",) * 2,
    ("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",) * 2,
    ("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",) * 2,
    # 思源黑体 / Noto CJK 简中专用（优先 SC 命名，避免 ttc 里取到日文变体）
    ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
     "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJKsc-Regular.otf",
     "/usr/share/fonts/noto-cjk/NotoSansCJKsc-Bold.otf"),
    ("/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
     "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Bold.otf"),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    # 其他常见兜底
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",) * 2,
    ("/usr/share/fonts/truetype/droid/DroidSansFallback.ttf",) * 2,
]

# ── macOS ──
_MAC_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc",) * 2,
    ("/System/Library/Fonts/Hiragino Sans GB.ttc",) * 2,
    ("/System/Library/Fonts/STHeiti Medium.ttc",) * 2,
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf",) * 2,
]

# Linux 目录扫描时按文件名特征匹配（不区分大小写）
_LINUX_NAME_RE = re.compile(
    r"(wqy|zenhei|microhei|noto.*cjk|source ?han|sarasa|droid ?sans ?fallback|"
    r"misans|hana|mincho.*sc|ukij|unifont)", re.I)
_LINUX_BOLD_RE = re.compile(r"(bold|black|heavy|bd)", re.I)
_LINUX_EXTS = (".ttf", ".ttc", ".otf")
_LINUX_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
    os.path.expanduser("~/.fonts"),
)

_pair_cache = None  # (reg, bold) 进程级缓存


def _first_existing(pairs):
    for pair in pairs:
        if all(os.path.isfile(p) for p in pair):
            return pair
    return None


def _fc_match_file(pattern):
    """fc-match 查询字体文件路径（fontconfig 不可用时返回 None）"""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                             capture_output=True, text=True, timeout=10)
        path = (out.stdout or "").strip()
        return path if path and os.path.isfile(path) else None
    except Exception:
        return None


def _scan_linux_dirs():
    """遍历字体目录，按文件名特征收集 (regular, bold) 候选，命中即返回"""
    found_reg = found_bold = None
    for base in _LINUX_DIRS:
        if not os.path.isdir(base):
            continue
        try:
            for root, _dirs, files in os.walk(base):
                for name in files:
                    if not name.lower().endswith(_LINUX_EXTS):
                        continue
                    if not _LINUX_NAME_RE.search(name):
                        continue
                    full = os.path.join(root, name)
                    if found_bold is None and _LINUX_BOLD_RE.search(name):
                        found_bold = full
                    if found_reg is None and not _LINUX_BOLD_RE.search(name):
                        found_reg = full
                if found_reg and found_bold:
                    return (found_reg, found_bold)
        except OSError:
            continue
    if found_reg:
        return (found_reg, found_bold or found_reg)
    return None


def _resolve_pair():
    global _pair_cache
    if _pair_cache is not None:
        return _pair_cache

    pair = None

    # 1. 环境变量显式指定
    env_reg = os.environ.get(ENV_REG, "").strip()
    env_bold = os.environ.get(ENV_BOLD, "").strip() or env_reg
    if env_reg and os.path.isfile(env_reg):
        pair = (env_reg, env_bold if os.path.isfile(env_bold) else env_reg)

    # 2. 平台候选清单
    if pair is None:
        if os.name == "nt":
            pair = _first_existing(_WIN_CANDIDATES)
        elif os.name == "posix" and os.path.isdir("/System/Library/Fonts"):
            pair = _first_existing(_MAC_CANDIDATES) or _first_existing(_LINUX_CANDIDATES)
        else:
            pair = _first_existing(_LINUX_CANDIDATES)

    # 3. Linux：目录扫描按文件名特征匹配
    if pair is None and os.name == "posix" and not os.path.isdir("/System/Library/Fonts"):
        pair = _scan_linux_dirs()

    # 4. fc-match 动态发现（有 fontconfig 的 Linux 基本都能命中）
    if pair is None:
        reg = _fc_match_file("lang=zh-cn")
        if reg:
            bold = _fc_match_file("lang=zh-cn:weight=bold") or reg
            pair = (reg, bold)

    # 5. 兜底：各平台首个候选（PIL 抛错时调用方回退文本）
    if pair is None:
        fallback = _LINUX_CANDIDATES[0] if os.name != "nt" else _WIN_CANDIDATES[0]
        pair = fallback

    _pair_cache = pair
    return pair


def resolve_font_pair():
    """返回 (常规体路径, 加粗体路径)"""
    return _resolve_pair()


def resolve_font(bold=False):
    """返回单个字体文件路径"""
    return _resolve_pair()[1 if bold else 0]
