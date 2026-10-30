"""Result-only interface to the pinned published loadout engine."""
from functools import lru_cache
import json
from pathlib import Path
import subprocess
from astrbot_plugin_hd2_common.data import setting

HERE=Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def catalog():
    return json.loads((HERE/"runtime/data/catalog.json").read_text(encoding="utf-8"))


def draw(mode="assessment"):
    if mode not in ("assessment","pure"):
        raise ValueError("未知抽取模式")
    node=setting("runtime","node",default="node")
    timeout=float(setting("runtime","draw_timeout_seconds",default=20))
    if not 1<=timeout<=120:
        raise ValueError("抽取超时配置需为1–120秒")
    try:
        run=subprocess.run([node,str(HERE/"draw_bridge.mjs"),mode],capture_output=True,text=True,
                           encoding="utf-8",timeout=timeout,check=True)
        ids=json.loads(run.stdout)
        if set(ids)!={"primary","secondary","grenade","stratagems"} or not isinstance(ids["stratagems"],list) or len(ids["stratagems"])!=4:
            raise ValueError("Invalid result")
        by_id={row["id"]:row for row in catalog()["equipment"]}
        flat=[ids["primary"],ids["secondary"],ids["grenade"],*ids["stratagems"]]
        if len(set(flat))!=7:
            raise ValueError("Invalid result")
        for slot in ("primary","secondary","grenade"):
            if by_id[ids[slot]]["selection_slot"]!=slot:
                raise ValueError("Invalid slot")
        if any(by_id[e]["selection_slot"]!="stratagem" for e in ids["stratagems"]):
            raise ValueError("Invalid slot")
        # Only display fields cross the Python presentation boundary.
        def item(eid):
            row=by_id[eid]
            return {key:row.get(key,"") for key in ("id","name","name_en")}
        return {**{slot:item(ids[slot]) for slot in ("primary","secondary","grenade")},
                "stratagems":[item(e) for e in ids["stratagems"]]}
    except FileNotFoundError as error:
        raise RuntimeError("抽取暂不可用，请检查 Node 运行时与资料安装") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("抽取超时，请稍后再试") from error
    except (subprocess.CalledProcessError,ValueError,KeyError,TypeError) as error:
        raise RuntimeError("抽取暂不可用，请稍后再试") from error


def format_result(result):
    lines=["🎲 战术配装"]
    for slot,label in (("primary","主手"),("secondary","副手"),("grenade","手雷")):
        lines.append(label+"："+(result[slot]["name"] or result[slot]["name_en"]))
    lines.extend(f"战备{i+1}："+(row["name"] or row["name_en"]) for i,row in enumerate(result["stratagems"]))
    return "\n".join(lines)
