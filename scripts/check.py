"""Offline checks and optional configuration preflight; never starts or sends."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))


def preflight(path):
    import hd2_config as cfg
    import os
    os.environ["HD2_CONFIG"] = str(path)
    value = cfg.load_config(force=True)
    mode = value["bot"]["platform"]
    if mode not in ("napcat", "qq_official"):
        raise ValueError("bot.platform 无效")
    if mode in ("napcat", "snowluma", "onebot") and not value.get("snowluma", {}).get("access_token"):
        raise ValueError("协议端（SnowLuma）缺少 snowluma.access_token；请在本机配置，勿提交凭据")
    if not value["astrbot"].get("data_dir"):
        raise ValueError("请指定 astrbot.data_dir")
    node = value.get("runtime", {}).get("node", "node")
    run = subprocess.run([node,"--version"],capture_output=True,text=True,check=True)
    if int(run.stdout.lstrip("v").split(".")[0]) < 22:
        raise ValueError("需要 Node.js 22 或更高版本")
    print("配置结构与 Node 运行时检查通过（未验证凭据或平台连接）")


def _boxes_overlap(a, b):
    """两个包围盒是否相交（含边界接触）。"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def check_text_layout():
    """任务 B：文字溢出离线检查（不联网、不需要 AstrBot、不写文件）。

    夹具一「超长说明文字」：折行后的每一行都必须落在区块宽度内，行数受限时末行省略号；
    夹具二「10 位数字 + 长标签 + 长副标签」：同行标签与数值包围盒不相交、也都不越界。
    """
    from PIL import Image, ImageDraw
    from astrbot_plugin_hd2_war_report import report_card as card

    errors = []
    width, pad = 1080, 36
    image = Image.new("RGB", (width, 600), card.BG)
    draw = ImageDraw.Draw(image)

    # ── 夹具一：超长说明文字 ──
    box = (pad, 24, width - pad, 200)
    inner_w = box[2] - box[0] - 24
    long_text = ("解放泽亚鲁戈西亚将对海德斯的光能者形成包围，切断其支援与增援。"
                 "固守该星可开启 1 处围攻解放，降低当地敌方抵抗力。") * 6
    for line in card.wrap_text(draw, long_text, card.F(17), inner_w):
        line_w = draw.textlength(line, font=card.F(17))
        if line_w > inner_w + 0.5:
            errors.append(f"超长说明折行越界：{line_w:.1f}px > {inner_w}px（{line[:18]}…）")
        bbox = draw.textbbox((box[0] + 12, box[1] + 24), line, font=card.F(17))
        if bbox[2] > box[2] - 12 + 0.5:
            errors.append(f"超长说明包围盒越界：right={bbox[2]} > {box[2] - 12}（{line[:18]}…）")
    limited = card.wrap_text(draw, long_text, card.F(17), inner_w, max_lines=3)
    if len(limited) != 3:
        errors.append(f"max_lines 未生效：得到 {len(limited)} 行")
    elif not limited[-1].endswith("…"):
        errors.append("max_lines 截断后末行缺少省略号")
    elif draw.textlength(limited[-1], font=card.F(17)) > inner_w + 0.5:
        errors.append("max_lines 截断后末行仍然越界")

    # ── 夹具二：10 位数字 + 长标签 + 长副标签 ──
    rows = [
        ("阵亡绝地潜兵（历史累计）", "31,485,995",
         "其他历史 终结族 131,976 · 机器人 2,331 · 光能者 0"),
        ("累计发数（本场战争累计）", "10,063,177,926", "命中/开火 102.7%"),
        ("在线玩家（占全服比例）", "12,489", "占全服 59.26%"),
        ("等效抵抗度（每百万血）", "5.00", "抵抗度 2.94%/h × 血量 1,700,000 ÷ 1,000,000"),
    ]
    row_x, row_w = pad + 18, width - pad * 2 - 36
    row_y = 240
    for label, value, note in rows:
        row_h, boxes = card.label_value_row(
            draw, row_x, row_y, row_w, label, value, card.F(15), card.F(21, True),
            note=note, note_font=card.F(13))
        row_box = (row_x, row_y - 8, row_x + row_w, row_y + row_h + 8)
        for key, bbox in boxes.items():
            if bbox is None:
                continue
            if bbox[0] < row_box[0] - 0.5 or bbox[2] > row_box[2] + 0.5:
                errors.append(f"{label} 的 {key} 水平越界：{bbox[0]}..{bbox[2]} / {row_box[0]}..{row_box[2]}")
            if bbox[1] < row_box[1] - 0.5 or bbox[3] > row_box[3] + 0.5:
                errors.append(f"{label} 的 {key} 垂直越界：{bbox[1]}..{bbox[3]} / {row_box[1]}..{row_box[3]}")
        label_bbox, value_bbox = boxes.get("label"), boxes.get("value")
        if label_bbox and value_bbox and _boxes_overlap(label_bbox, value_bbox):
            errors.append(f"{label} 的标签与数值包围盒相交：{label_bbox} / {value_bbox}")
        note_bbox = boxes.get("note")
        if note_bbox and value_bbox and _boxes_overlap(note_bbox, value_bbox):
            errors.append(f"{label} 的副标签与数值包围盒相交：{note_bbox} / {value_bbox}")
        if note_bbox and label_bbox and _boxes_overlap(note_bbox, label_bbox):
            errors.append(f"{label} 的副标签与标签包围盒相交：{note_bbox} / {label_bbox}")
        row_y += row_h + 6

    if errors:
        raise SystemExit("文字布局检查失败：\n  - " + "\n  - ".join(errors))
    print("文字布局检查通过（超长说明折行 / 10 位数字与长标签不重叠不越界）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.config:
        preflight(args.config)
        return
    for path in [*ROOT.glob("*.py"), *ROOT.joinpath("plugins").rglob("*.py"), *ROOT.joinpath("scripts").glob("*.py")]:
        ast.parse(path.read_text(encoding="utf-8-sig"),filename=str(path))
    check_text_layout()
    subprocess.run([sys.executable,"-X","utf8","-m","unittest","discover","-s","tests","-v"],cwd=ROOT,check=True)


if __name__ == "__main__":
    main()
