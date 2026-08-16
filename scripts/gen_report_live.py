import json, sys, io, os, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 配置化：环境变量 HD2_PROJECT_DIR 指向项目根（含 config.json）
_ROOT = os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
    def _g(*k, d=None):
        return _cfg.get(*k, default=d)
except Exception:
    def _g(*k, d=None):
        return d

# AstrBot 环境 + war_report 插件
sys.path.insert(0, _g("astrbot", "app_dir", d=""))
sys.path.insert(0, os.path.join(_ROOT, "plugins", "astrbot_plugin_hd2_war_report"))
import main

# LLM：优先环境变量 DEEPSEEK_API_KEY，其次 config.json 的 llm.api_key
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _g("llm", "api_key", d="")
API_URL = _g("llm", "api_url", d="https://api.deepseek.com/v1/chat/completions")
if not API_KEY:
    print("❌ 未配置 llm.api_key（或环境变量 DEEPSEEK_API_KEY）")
    sys.exit(1)

TERM_GLOSSARY = """
游戏术语对照表（翻译时必须使用这些译名）：
- DARIUS II = 大流士 II
- ACHERNAR SECUNDUS = 水委一次星
- GRAND ERRANT = 大艾伦特
- PHERKAD SECUNDUS = 北极一次星
- MEISSA = 梅莎
- Terminids = 终结族（虫族）
- Automatons = 机器人
- Illuminate = 光能者
- Cyborgs = 生化人
- cyborg = 生化人
- destryers = 超级驱逐舰（前面带 super 时译文不变，如 super destryers 仍译为超级驱逐舰）
- Alcubiere = 阿库别瑞
- MegaFactory = 超大型工厂
- Megafactories = 超大型工厂
- TCS+ = TCS+
- Major Order = 重要指令
- STRATEGIC IMPERATIVE = 战略机遇
- Super Earth = 超级地球
- Helldivers = 绝地潜兵
- Barrier planets = 屏障星球
- Stratagem = 战略/战略配备
- The Void = 寂域
- Void-impacted molecules = 受寂域影响的分子
- Voteless = 无票者
- Ministry of Science = 科学部
- y-shapes = 音叉形状
- Socialism = （不要翻译，遇到直接略过该单词，不要输出任何中文对应词）
"""

def llm_translate(text: str, system: str) -> str:
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

pm = main._load_planet_map()
mo = main._get_major_order(pm, brief_translated=None)

items = main._get_news_items(1)
news_block = ""
if items:
    raw = main._clean_news_message(items[0].get("message", ""), 1500)
    system = (
        "你是《绝地潜兵2》官方新闻的中文本地化翻译。把用户给出的英文新闻完整翻译成简体中文，"
        "保留所有段落和细节，不要省略、不要总结、不要压缩，不要添加解释。\n\n"
        + TERM_GLOSSARY
    )
    news_zh = llm_translate(raw, system)
    # 本地术语兜底修正（防 LLM 不遵循术语表）
    for old, new in main.TERM_FIXES:
        news_zh = news_zh.replace(old, new)
    news_block = "📰 最新资讯：\n" + news_zh

parts = []
if mo:
    parts.append(mo)
else:
    parts.append("⚠️ 暂时无法获取 Major Order 数据。")

warzone = main._get_warzone_distribution()
if warzone:
    parts.append(warzone)

if news_block:
    parts.append(news_block)
report = "\n\n".join(parts)

# 直接写 UTF-8 文件（不经 PowerShell 管道）
out_file = os.path.join(_ROOT, "temp", "report_live2.txt")
os.makedirs(os.path.dirname(out_file), exist_ok=True)
with open(out_file, 'w', encoding='utf-8') as f:
    f.write(report)
print("saved, len:", len(report))
print(report[:150])
