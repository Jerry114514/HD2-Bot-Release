import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from test_core import ROOT
from framework import install
install()
from astrbot_plugin_hd2_common import diagnostics
from astrbot_plugin_hd2_roll import loadout, main as roll


class LoadoutTests(unittest.TestCase):
    def test_real_bridge_returns_only_equipment_and_passes_published_core(self):
        samples=[]
        for mode in ("assessment",)*5+("pure",):
            run=subprocess.run(["node",str(loadout.HERE/"draw_bridge.mjs"),mode],capture_output=True,text=True,check=True)
            value=json.loads(run.stdout)
            self.assertEqual(set(value),{"primary","secondary","grenade","stratagems"})
            self.assertEqual(len(set([value["primary"],value["secondary"],value["grenade"],*value["stratagems"]])),7)
            self.assertLessEqual(sum(x.startswith("vehicle_exo_") for x in value["stratagems"]),1)
            samples.append(value)
        # Internal-only verification against the unchanged published evaluator.
        script="""import{readFileSync}from'node:fs';import{evaluateAssessment,assessmentLegality}from'./plugins/astrbot_plugin_hd2_roll/runtime/assessment-v2/evaluate.mjs';
const base='./plugins/astrbot_plugin_hd2_roll/runtime/';const model=JSON.parse(readFileSync(base+'assessment-v2/equipment.json'));const catalog=JSON.parse(readFileSync(base+'data/catalog.json'));
const values=JSON.parse(readFileSync(process.argv[1]));for(let i=0;i<values.length;i++){if(assessmentLegality(values[i],catalog,model).status!=='PASS')throw Error('illegal');if(i<5){const result=evaluateAssessment(values[i],model,catalog);if(result.status!=='ESTIMATED_PASS'||!result.acceptance_ready)throw Error('rejected');}}"""
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,"samples.json");path.write_text(json.dumps(samples))
            subprocess.run(["node","--input-type=module","-e",script,str(path)],cwd=ROOT,check=True,capture_output=True)

    def test_render_failure_never_redraws_or_exposes_details(self):
        from framework import Event
        fake={"primary":{"name":"主手"},"secondary":{"name":"副手"},"grenade":{"name":"手雷"},"stratagems":[{"name":str(i)} for i in range(4)]}
        async def run():
            event=Event("/roll")
            return [value async for value in roll.Hd2RollPlugin(None).keyword_trigger(event)]
        with tempfile.TemporaryDirectory() as temp,patch.object(diagnostics,"LOG_FILE",str(Path(temp)/"log")),patch.object(roll,"draw",return_value=fake) as draw,patch.object(roll,"render",side_effect=ImportError("no image")):
            output=asyncio.run(run())
            self.assertEqual(draw.call_count,1)
            self.assertEqual(output,[("text",loadout.format_result(fake))])
            for hidden in ("评分","模型","seed","assessment","completed","尝试"):
                self.assertNotIn(hidden,output[0][1])

    def test_unavailable_bridge_never_falls_back_to_random(self):
        for error in (subprocess.TimeoutExpired("node",1),subprocess.CalledProcessError(1,"node",stderr="internal scores and seed")):
            with patch.object(loadout.subprocess,"run",side_effect=error) as run:
                with self.assertRaises(RuntimeError) as failure:loadout.draw()
                self.assertNotIn("scores",str(failure.exception))
                self.assertEqual(run.call_count,1)
        with patch.object(loadout.subprocess,"run",return_value=types.SimpleNamespace(stdout='{"primary":"x","seed":1}')):
            with self.assertRaises(RuntimeError):loadout.draw()

    def test_replacement_and_lock_commands_are_not_supported(self):
        from framework import Event
        async def run(text):
            return [v async for v in roll.Hd2RollPlugin(None).keyword_trigger(Event(text))]
        with patch.object(roll,"draw") as draw:
            for text in ("/roll 换一件","/roll replace","/roll 锁定"):
                output=asyncio.run(run(text))
                self.assertIn("用法",output[0][1])
            draw.assert_not_called()


if __name__=="__main__":unittest.main()
