"""Port 8795 is already used by a dashboard on some machines: serve.py must refuse a taken port, and it must
stop before binding when the config file is missing so it never briefly occupies a port it cannot serve.
The tests build their own valid config instead of depending on hosts.example.json staying present and valid."""
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PORTS_ALREADY_IN_USE_ON_THIS_MACHINE = {8080, 18080, 18081, 8102, 8103, 8795, 11434, 18789, 8384, 22000}
PY = sys.executable if sys.version_info >= (3, 12) else shutil.which("python3.12") or sys.executable


def occupied_port():
    """A socket holding a port; never one of the ports this machine already uses, even if the OS picks one."""
    for _ in range(10):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        if s.getsockname()[1] not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE:
            return s
        s.close()
    raise AssertionError("the OS kept assigning ephemeral ports that this machine already uses")


@pytest.fixture
def good_config(tmp_path):
    cfg = tmp_path / "hosts.json"
    cfg.write_text(json.dumps({"hub": {"name": "hub"}, "hosts": []}))
    return str(cfg)


def test_serve_refuses_a_port_that_is_already_taken(good_config):
    holder = occupied_port()
    port = holder.getsockname()[1]
    try:
        r = subprocess.run([PY, str(HERE / "serve.py"), "--port", str(port), "--config", good_config], cwd=HERE,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    finally:
        holder.close()
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "Traceback" not in out
    assert "already in use" in out and str(port) in out
    assert "--port" in out and "tailscale serve" in out


def test_serve_reads_nothing_when_the_port_is_taken(good_config):
    holder = occupied_port()
    port = holder.getsockname()[1]
    try:
        r = subprocess.run([PY, str(HERE / "serve.py"), "--port", str(port), "--config", good_config], cwd=HERE,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    finally:
        holder.close()
    assert "refreshed" not in r.stdout


def test_serve_stops_before_binding_when_the_config_is_missing(monkeypatch, tmp_path):
    import serve
    missing = tmp_path / "no-hosts.json"
    bound = {}

    def fake_server(addr, handler):
        bound["addr"] = addr
        raise AssertionError("serve must not bind when the config is missing")

    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer", fake_server)
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(missing)])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert "addr" not in bound                          # binding was never attempted
    msg = str(e.value)
    assert "not found" in msg and "nothing was read or bound" in msg
    assert "already in use" not in msg


def test_serve_defaults_to_8795(monkeypatch, good_config):
    import serve
    attempted = {}

    def fake_server(addr, handler):
        attempted["addr"] = addr
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer", fake_server)
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", good_config])
    with pytest.raises(SystemExit):
        serve.main()
    assert attempted["addr"] == ("127.0.0.1", 8795)
