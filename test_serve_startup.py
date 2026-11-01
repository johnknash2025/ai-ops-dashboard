"""serve.py must actually start and keep refreshing when the config is good and the port is free (not only
refuse bad cases), must stop before binding when hosts.json is broken JSON or shaped wrong, and when a refresh
fails it must keep serving the last good page and say so instead of failing silently. Nothing here binds a port
this machine already uses."""
import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import chat
import collect
import serve

PORTS_ALREADY_IN_USE_ON_THIS_MACHINE = {8080, 18080, 18081, 8102, 8103, 8795, 11434, 18789, 8384, 22000}
PY = sys.executable
HERE = Path(__file__).parent


def free_socket():
    """A free ephemeral port; never one of the ports this machine already uses, even if the OS picks one."""
    for _ in range(10):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        if s.getsockname()[1] not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE:
            return s
        s.close()
    raise AssertionError("the OS kept assigning ephemeral ports that this machine already uses")


def start_server_and_refresher(monkeypatch, tmp_path, fake, every=0.2):
    stop = threading.Event()

    def managed(config, yes=False):
        if stop.is_set():
            raise SystemExit("the test asked the refresher to stop")   # BaseException: leaves refresher's except Exception
        fake(config, yes)

    monkeypatch.setattr(collect, "HERE", tmp_path)
    monkeypatch.setattr(collect, "collect", managed)
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def loop():
        try:
            serve.refresher(tmp_path / "hosts.json", every)
        except SystemExit:
            pass                                    # the stop request: end the thread the normal way

    refresher = threading.Thread(target=loop, daemon=True)
    refresher.start()
    return server, port, refresher, stop


def get(port, path="/", method="GET", body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode()


def wait_until(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def isolate_chat(monkeypatch, tmp_path, history_of=None):
    """Keep chat.py away from the ~/.ai-ops of whoever runs the tests: chat logs and orders live in tmp_path, and
    a requested agent's history is whatever this test wrote, so the answers never depend on the machine."""
    (tmp_path / "chat").mkdir(exist_ok=True)
    for name, lines in (history_of or {}).items():
        body = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in lines)
        (tmp_path / "chat" / f"{name}.jsonl").write_text(body, encoding="utf-8")
    monkeypatch.setattr(chat, "CHATS", tmp_path / "chat")
    monkeypatch.setattr(chat, "ORDERS", tmp_path / "orders.jsonl")
    return tmp_path / "chat"


def wait_for_output(streams, wanted, timeout=30):
    """streams is a list of (pipe, collected) pairs. The child's output is collected without ever blocking on a
    line: select says which pipe has data and os.read takes only what is there. A child that prints nothing,
    prints only on stderr, or dies ends the wait by timeout, never by a readline() that hangs the test."""
    deadline, eof = time.time() + timeout, set()
    while time.time() < deadline:
        watching = [(f, buf) for f, buf in streams if f is not None and f not in eof]
        if not watching:
            break
        ready, _, _ = select.select([f for f, _ in watching], [], [], 0.2)
        for f in ready:
            chunk = os.read(f.fileno(), 8192)
            if chunk:
                next(buf for x, buf in watching if x is f).append(chunk.decode("utf-8", "replace"))
            else:
                eof.add(f)                      # that pipe is closed: stop watching it, do not spin on EOF
        if wanted("".join("".join(buf) for _, buf in streams)):
            break


def test_a_good_config_on_a_free_port_really_starts_and_the_refresh_loop_updates_the_page(monkeypatch, tmp_path):
    calls = []

    def fake(config, yes=False):
        calls.append(config)
        (tmp_path / "dashboard.html").write_text(f"page {len(calls)}")

    server, port, _, stop = start_server_and_refresher(monkeypatch, tmp_path, fake)
    try:
        assert wait_until(lambda: len(calls) >= 3), "the every-few-minutes refresh loop did not run repeatedly"
        status, body = get(port)                       # the served page is the one the refresher last wrote
        assert status == 200
        assert body.startswith("page ") and int(body.split()[-1]) >= 3
    finally:
        stop.set()
        server.shutdown()


def test_the_started_process_answers_on_the_port_it_claims_and_its_loop_runs(tmp_path):
    cfg = tmp_path / "hosts.json"
    cfg.write_text(json.dumps({"hub": {"name": "hub"}, "hosts": [{"name": "box", "ssh": []}]}))
    holder = free_socket()
    port = holder.getsockname()[1]
    holder.close()
    env = {"AI_OPS_CONFIG_DIR": str(tmp_path / "conf"), "PATH": "/usr/bin:/bin"}
    p = subprocess.Popen([PY, str(HERE / "serve.py"), "--port", str(port), "--every", "120", "--config", str(cfg)],
                         cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, env=env)
    out, errs = [], []
    streams = [(p.stdout, out), (p.stderr, errs)]
    try:
        wait_for_output(streams, lambda s: f"127.0.0.1:{port}" in s)
        served, err_text = "".join(out), "".join(errs)
        assert p.poll() is None, f"the process died before it served anything (stdout: {served!r}, stderr: {err_text!r})"
        assert "serving" in served and f"127.0.0.1:{port}" in served and "Traceback" not in served, \
            f"the process did not report the port it claims (stdout: {served!r}, stderr: {err_text!r})"

        # the refresh loop must have reported its first pass; a loop that stays silent fails the test, it must not hang it
        wait_for_output(streams, lambda s: "refreshed" in s or "refresh failed" in s, timeout=40)
        served, err_text = "".join(out), "".join(errs)
        assert "refreshed" in served or "refresh failed" in served, \
            f"the refresh loop never reported a pass (stdout: {served!r}, stderr: {err_text!r})"
        try:
            status, _ = get(port)                      # a response at all proves the port is really served
        except urllib.error.HTTPError as e:
            status = e.code
        assert status in (200, 404)
    finally:
        p.terminate()
        p.wait(timeout=10)
        for pipe in (p.stdout, p.stderr):
            if pipe is not None:
                pipe.close()


def test_a_failed_refresh_keeps_the_last_good_page_and_names_the_failure(monkeypatch, tmp_path, capsys):
    state = {"n": 0}

    def fake(config, yes=False):
        state["n"] += 1
        if state["n"] == 1:
            (tmp_path / "dashboard.html").write_text("the good page")
        else:
            raise RuntimeError("probe exploded")

    server, port, refresher, stop = start_server_and_refresher(monkeypatch, tmp_path, fake)
    try:
        assert wait_until(lambda: state["n"] >= 3), "the refresher stopped trying after a failure"
        status, body = get(port)
        assert status == 200 and body == "the good page"    # the old dashboard stays served
        assert refresher.is_alive()
        out = capsys.readouterr().out
        assert "refresh failed" in out and "the last good dashboard stays served" in out
        assert "last refresh ok:" in out
    finally:
        stop.set()
        server.shutdown()


def test_serve_refuses_a_config_that_is_not_valid_JSON_before_touching_the_port(monkeypatch, tmp_path):
    bad = tmp_path / "hosts.json"
    bad.write_text('{ "hosts": [ oops ]')
    bound = []
    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer",
                        lambda addr, handler: bound.append(addr) or (_ for _ in ()).throw(AssertionError("must not bind")))
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(bad)])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert not bound
    msg = str(e.value)
    assert "not valid JSON" in msg and str(bad) in msg and "nothing was read or bound" in msg.lower()
    assert "already in use" not in msg


def test_serve_refuses_a_config_without_hub_and_hosts_before_touching_the_port(monkeypatch, tmp_path):
    bad = tmp_path / "hosts.json"
    bad.write_text(json.dumps({"hosts": []}))
    bound = []
    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer",
                        lambda addr, handler: bound.append(addr) or (_ for _ in ()).throw(AssertionError("must not bind")))
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(bad)])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert not bound
    msg = str(e.value)
    assert "hub" in msg and "hosts" in msg and "hosts.example.json" in msg


def test_a_good_config_passes_validation_and_reaches_the_port_check(monkeypatch, tmp_path):
    good = tmp_path / "hosts.json"
    good.write_text(json.dumps({"hub": {"name": "hub"}, "hosts": [{"name": "box", "ssh": []}]}))
    attempted = {}

    def fake_server(addr, handler):
        attempted["addr"] = addr
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer", fake_server)
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(good), "--port", "18923"])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert attempted["addr"] == ("127.0.0.1", 18923)      # validation passed, the port check ran
    assert "already in use" in str(e.value) and "18923" in str(e.value)


def test_the_api_answers_503_when_collected_data_is_missing_or_broken(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "HERE", tmp_path)
    isolate_chat(monkeypatch, tmp_path, {"reviewer": [{"time": "2026-10-26 06:49:00", "role": "agent",
                                                       "text": "the answer this test wrote"}]})
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"X-AiOps": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    try:
        for broken in (None, "not json at all {"):
            if broken is None:
                (tmp_path / "last.json").unlink(missing_ok=True)
            else:
                (tmp_path / "last.json").write_text(broken)
            with pytest.raises(urllib.error.HTTPError) as e:
                get(port, "/api/ask", "POST", b'{"agent": "reviewer", "text": "hi"}', headers)
            assert e.value.code == 503, "the server must answer the API instead of crashing on bad data"
        status, body = get(port, "/api/history?agent=reviewer")
        assert status == 200                               # the server stayed alive through the bad requests
        assert "the answer this test wrote" in body        # a known history, not whatever this machine happens to hold
    finally:
        server.shutdown()


def test_an_unreadable_last_json_answers_503_instead_of_crashing_the_handler(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "HERE", tmp_path)
    monkeypatch.setattr(collect, "CFG", {"hub": {"name": "hub"}, "hosts": [{"name": "box", "ssh": []}]})
    isolate_chat(monkeypatch, tmp_path, {"reviewer": [{"time": "2026-10-26 06:49:00", "role": "agent",
                                                       "text": "still answering"}]})
    (tmp_path / "last.json").mkdir()                       # a folder where the API expects a JSON file
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"X-AiOps": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            get(port, "/api/ask", "POST", b'{"agent": "reviewer", "text": "hi"}', headers)
        assert e.value.code == 503, "an unreadable last.json must be answered, not left as a handler crash"
        assert "last.json" in e.value.reason
        status, body = get(port, "/api/history?agent=reviewer")
        assert status == 200 and "still answering" in body   # the same server is still serving afterwards
    finally:
        server.shutdown()


def test_an_unreadable_config_path_stops_before_touching_the_port(monkeypatch, tmp_path):
    bad = tmp_path / "hosts.json"
    bad.mkdir()                                            # a directory where serve.py expects a JSON file
    bound = []
    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer",
                        lambda addr, handler: bound.append(addr) or (_ for _ in ()).throw(AssertionError("must not bind")))
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(bad)])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert not bound
    msg = str(e.value)
    assert str(bad) in msg and "could not be read" in msg and "Traceback" not in msg
    assert "already in use" not in msg


def test_the_api_answers_503_before_the_first_refresh_finishes(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "HERE", tmp_path)
    monkeypatch.setattr(collect, "CFG", {})                # exactly what the module holds before any refresh
    isolate_chat(monkeypatch, tmp_path, {"reviewer": [{"time": "2026-10-26 06:49:00", "role": "agent",
                                                       "text": "alive while starting"}]})
    (tmp_path / "last.json").write_text(json.dumps({"hosts": []}))   # stale-good data on disk
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"X-AiOps": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            get(port, "/api/ask", "POST", b'{"agent": "reviewer", "text": "hi"}', headers)
        assert e.value.code == 503, "a request before the first refresh must be refused, not crash the handler"
        assert "still starting" in e.value.reason
        status, body = get(port, "/api/history?agent=reviewer")
        assert status == 200 and "alive while starting" in body   # the server stayed alive through the too-early request
    finally:
        server.shutdown()


def test_the_api_answers_503_while_the_config_is_only_half_filled(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "HERE", tmp_path)
    monkeypatch.setattr(collect, "CFG", {"hub": {}})       # not empty, yet no hosts list: a refresh is still running
    isolate_chat(monkeypatch, tmp_path, {"reviewer": [{"time": "2026-10-26 06:49:00", "role": "agent",
                                                       "text": "waiting for the config"}]})
    (tmp_path / "last.json").write_text(json.dumps({"hosts": []}))
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"X-AiOps": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    try:
        for half in ({"hub": {}}, {"hosts": []}, {"hub": {}, "hosts": None}, {"hub": [], "hosts": []}):
            monkeypatch.setattr(collect, "CFG", half)
            with pytest.raises(urllib.error.HTTPError) as e:
                get(port, "/api/ask", "POST", b'{"agent": "reviewer", "text": "hi"}', headers)
            assert e.value.code == 503, f"a half-filled config {half} must not be used by the API"
            assert "still starting" in e.value.reason
        status, body = get(port, "/api/history?agent=reviewer")
        assert status == 200 and "waiting for the config" in body
    finally:
        server.shutdown()


def test_config_ready_accepts_only_a_whole_hosts_json_shape():
    assert serve.config_ready({"hub": {"name": "hub"}, "hosts": []})       # what a finished refresh fills
    for half in ({}, None, [], {"hub": {}}, {"hosts": []}, {"hub": {}, "hosts": None}, {"hub": None, "hosts": []},
                  {"hub": [], "hosts": []}, "hosts.json"):
        assert not serve.config_ready(half), f"{half!r} is not a whole config and must not be served as one"


def test_a_whole_config_and_good_data_reach_the_api_at_all(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "HERE", tmp_path)
    monkeypatch.setattr(collect, "CFG", {"hub": {"name": "hub"}, "hosts": [{"name": "box", "ssh": []}],
                                          "agents": [{"name": "reviewer", "layer": "owner"}]})
    isolate_chat(monkeypatch, tmp_path)
    (tmp_path / "last.json").write_text(json.dumps({"box": {"timers": []}}))
    server = serve.http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    assert port not in PORTS_ALREADY_IN_USE_ON_THIS_MACHINE
    threading.Thread(target=server.serve_forever, daemon=True).start()
    headers = {"X-AiOps": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    try:
        status, body = get(port, "/api/orders/ack", "POST", b'{"ids": []}', headers)
        assert status == 200 and json.loads(body) == {"ok": 0}, "a finished config must be served, not refused"
    finally:
        server.shutdown()


def test_serve_refuses_a_config_that_is_not_utf8_text_before_touching_the_port(monkeypatch, tmp_path):
    bad = tmp_path / "hosts.json"
    bad.write_bytes(b'{"hub": {"name": "hub"}, "hosts": [\xff\xfe\x00binary]}')   # not text at all
    bound = []
    monkeypatch.setattr(serve.http.server, "ThreadingHTTPServer",
                        lambda addr, handler: bound.append(addr) or (_ for _ in ()).throw(AssertionError("must not bind")))
    monkeypatch.setattr(sys, "argv", ["serve.py", "--config", str(bad)])
    with pytest.raises(SystemExit) as e:
        serve.main()
    assert not bound, "a config that is not UTF-8 text must stop the start before any bind"
    msg = str(e.value)
    assert str(bad) in msg and "UTF-8" in msg and "nothing was read or bound" in msg.lower()
    assert "Traceback" not in msg and "already in use" not in msg
