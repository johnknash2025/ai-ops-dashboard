"""`--yes` approves every host in the config without asking: the consent gate must stay honest about that,
and the --help text must tell you to check hosts.json before using it."""
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PY = sys.executable if sys.version_info >= (3, 12) else shutil.which("python3.12") or sys.executable
HOW = "ssh probe@192.0.2.21"


@pytest.fixture
def collect(tmp_path):
    old = os.environ.get("AI_OPS_CONFIG_DIR")
    os.environ["AI_OPS_CONFIG_DIR"] = str(tmp_path / "conf")
    module = importlib.reload(importlib.import_module("collect"))
    yield module
    if old is None:
        del os.environ["AI_OPS_CONFIG_DIR"]
    else:
        os.environ["AI_OPS_CONFIG_DIR"] = old


def test_yes_grants_consent_without_asking_and_says_what_it_granted(collect, tmp_path, capsys):
    assert collect.consented("workstation-7", HOW, yes=True) is True
    printed = capsys.readouterr().out
    assert "workstation-7" in printed and "192.0.2.21" in printed
    store = json.loads((tmp_path / "conf" / "consent.json").read_text())
    assert "workstation-7" in store


def test_without_yes_a_run_that_cannot_ask_is_not_allowed(collect, tmp_path, capsys):
    assert collect.consented("workstation-7", HOW, yes=False) is False
    assert "workstation-7" in capsys.readouterr().out
    assert not (tmp_path / "conf" / "consent.json").exists()


def test_a_second_run_with_the_same_probe_does_not_ask_again(collect, tmp_path, capsys):
    assert collect.consented("workstation-7", HOW, yes=True) is True
    capsys.readouterr()
    assert collect.consented("workstation-7", HOW, yes=False) is True
    assert collect.consented("workstation-7", HOW + " (installed probe)", yes=False) is False


def test_the_yes_help_text_tells_you_to_check_the_hosts_file():
    r = subprocess.run([PY, str(HERE / "collect.py"), "--help"], cwd=HERE,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "hosts.json" in r.stdout and "without asking" in r.stdout and "checking" in r.stdout
