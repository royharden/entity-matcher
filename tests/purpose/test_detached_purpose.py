"""Actual six purpose controls and two canaries after moving exact canonical modules."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_purpose_closure_detached_from_owner_consumer_and_network(tmp_path):
    notice = json.loads((ROOT / "VENDORED/S3-purpose-fix/SOURCE.json").read_bytes())
    for entry in notice["entries"][:14]:
        relative = entry["target"].removeprefix("src/entity_matcher/shared/")
        dest = tmp_path / "portable_s3" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / entry["target"]).read_bytes())
    target = tmp_path / "tests"
    files = [
        "conftest.py",
        "test_guards.py",
        "callledger/conftest.py",
        "callledger/test_child_purpose_reuse.py",
        "providers/conftest.py",
        "providers/test_child_purpose_controller.py",
    ]
    for name in files:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / "tests/purpose" / name).read_text(encoding="utf-8")
        dest.write_text(text.replace("entity_matcher.shared", "portable_s3"), encoding="utf-8")
    script = f"""
import importlib.abc,json,socket,sys
from pathlib import Path
sys.path.insert(0,{str(tmp_path)!r})
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.split('.')[0] in (
            {{'llm_router_eval','entity_matcher','openai','typesafe','httpx'}}
        ): raise RuntimeError('owner/consumer/provider disabled')
sys.meta_path.insert(0,Guard())
def denied(*a,**k): raise RuntimeError('network disabled')
socket.create_connection=denied;socket.socket.connect=denied
import pytest
code=pytest.main([{str(target)!r},'-q','--tb=short',
    '--junitxml='+{str(tmp_path / "proof.xml")!r}])
for name,module in tuple(sys.modules.items()):
    if name=='portable_s3' or name.startswith('portable_s3.'):
        Path(module.__file__).resolve().relative_to(Path({str(tmp_path / "portable_s3")!r}))
assert not any(n.split('.')[0] in {{'entity_matcher','llm_router_eval'}} for n in sys.modules)
raise SystemExit(code)
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    run = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    suites = list(ET.parse(tmp_path / "proof.xml").getroot().iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites) == 8
    assert all(
        int(s.attrib.get(k, 0)) == 0 for s in suites for k in ("failures", "errors", "skipped")
    )
