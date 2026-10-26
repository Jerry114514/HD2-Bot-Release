# -*- coding: utf-8 -*-
"""离线冒烟检查：语法编译全部 Python 文件 + 运行 unittest 套件。

用法：
    python scripts/smoke_check.py           # 纯离线（编译 + 单测）
    python scripts/smoke_check.py --live    # 附加真实数据链路冒烟（需外网）
"""
import compileall
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("HD2_PROJECT_DIR", ROOT)

import conftest  # noqa: F401,E402  (astrbot 桩)


def run_compile() -> bool:
    print("== 1) 语法编译 ==")
    ok = compileall.compile_dir(ROOT, quiet=1, force=True, maxlevels=6,
                                rx=None, ddir=None)
    # compileall 只保证语法；把无 __init__ 的插件目录也逐个编一遍
    import py_compile
    import glob
    failed = []
    for f in glob.glob(os.path.join(ROOT, "plugins", "*", "main.py")) + \
             glob.glob(os.path.join(ROOT, "hd2_*.py")) + \
             glob.glob(os.path.join(ROOT, "scripts", "*.py")):
        try:
            py_compile.compile(f, doraise=True)
        except Exception as e:
            failed.append((f, e))
    if failed:
        for f, e in failed:
            print(f"  编译失败 {f}: {e}")
        return False
    print("  全部 Python 文件编译通过")
    return bool(ok) or not failed


def run_tests() -> bool:
    print("== 2) 离线单元测试 ==")
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"))
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


def run_live() -> bool:
    """真实数据链路冒烟：主站 data.json -> MO/战线/DSS/归属/星球区域 全格式化一遍"""
    print("== 3) 真实数据链路冒烟（需外网）==")
    import hd2_datasource as ds
    import astrbot_plugin_hd2_war_report.main as wr
    import astrbot_plugin_hd2_planet_info.main as pi

    doc = ds.site_doc(force=True)
    if not doc:
        print("  [FAIL] 主站 data.json 不可达")
        return False
    print(f"  data.json fetchedAt={doc.get('fetchedAt')} source={doc.get('source')}")
    checks = []
    a = ds.mo_assignment(doc)
    pm = ds.planet_map(doc)
    mo = wr._format_mo_site(a, pm, None, None)
    checks.append(("MO 报告", bool(mo) and "🎖️" in mo))
    wz = wr._get_warzone_distribution()
    checks.append(("战区分布", bool(wz) and "总计" in wz))
    camps = wr._get_campaigns(pm)
    checks.append(("战线", bool(camps) and ("⚔️" in camps or "无" in camps)))
    dss = ds.dss(doc)
    if dss.get("planetIndex") is not None:
        dss_out = wr._format_dss_site(dss, pm)
        checks.append(("DSS", bool(dss_out) and "DSS" in dss_out))
    own = wr._get_ownership_changes(24)
    checks.append(("归属变化", bool(own) and "归属变化" in own))
    info = pi._fetch_planet_site(7)
    if info:
        pinfo = pi._translate_planet_info(info)
        checks.append(("星球详情(7)", bool(pinfo) and "抵抗" in pinfo or "解放" in pinfo))
    ok = True
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    return ok


def main() -> int:
    ok1 = run_compile()
    ok2 = run_tests()
    ok3 = True
    if "--live" in sys.argv:
        ok3 = run_live()
    print("== 结果 ==")
    print(f"  编译: {'OK' if ok1 else 'FAIL'}  单测: {'OK' if ok2 else 'FAIL'}"
          + (f"  实链路: {'OK' if ok3 else 'FAIL'}" if "--live" in sys.argv else ""))
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
