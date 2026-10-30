"""Optional real AstrBot SDK import smoke; no framework service or transport startup."""
import importlib
import importlib.metadata
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"plugins"))


def main():
    sandbox=ROOT/".qa-artifacts/sdk-smoke"
    sandbox.mkdir(parents=True,exist_ok=True)
    os.chdir(sandbox)
    os.environ["ASTRBOT_ROOT"]=str(sandbox)
    os.environ["HD2_PROJECT_DIR"]=str(ROOT)
    # Imports must not contact a platform, model, or telemetry endpoint.
    def offline(*args,**kwargs):
        raise OSError("SDK smoke is offline")
    socket.socket.connect=offline
    socket.create_connection=offline
    from astrbot.api.star import Star
    for path in sorted((ROOT/"plugins").glob("*/main.py")):
        module=importlib.import_module(path.parent.name+".main")
        classes=[value for value in vars(module).values() if isinstance(value,type) and value is not Star and issubclass(value,Star) and value.__module__==module.__name__]
        if not classes:
            raise RuntimeError("No plugin class: "+path.parent.name)
        print("SDK import OK:",path.parent.name)
    print("AstrBot",importlib.metadata.version("AstrBot"),"SDK imports passed; no service started")


if __name__=="__main__":main()
