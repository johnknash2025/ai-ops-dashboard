#!/usr/bin/env python3
"""Serve the dashboard from an always-on machine and refresh it every few minutes.

    python3 serve.py [--bind 127.0.0.1] [--port 8795] [--every 600]

Binds to 127.0.0.1 by default: put it on your tailnet with `tailscale serve` (HTTPS, tailnet only) instead of
opening a port. Only the two generated pages are served; nothing else in this folder is reachable.
Consent is not asked here: run collect.py once in a terminal on this machine and answer y for each host.
It checks the config file before touching the port: with no hosts.json, or with a hosts.json that is not UTF-8 text
or not valid JSON shaped like hosts.example.json, it names that file and binds nothing, so a broken config never
starts a server.
If the port is already taken (8795 is a common choice, so some machines run something else there) it stops with a
message naming the port instead of binding; it never shares a port and never exposes someone else's service.
A failed refresh never removes the last good page: it logs the failure, names the last good refresh, and keeps
serving what worked before while it tries again.
"""
import argparse
import http.server
import sys
import threading
import time
import traceback

import json
import urllib.parse

import chat
import collect

PAGES = {"/": "dashboard.html", "/dashboard.html": "dashboard.html", "/network.html": "network.html",
         "/inventory.json": "last.json"}   # the same data as the page, for other agents on the tailnet


def config_ready(cfg):
    """True only when the config is a whole hosts.json-shaped object, not a half-filled one."""
    return isinstance(cfg, dict) and isinstance(cfg.get("hub"), dict) and isinstance(cfg.get("hosts"), list)


class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path, q):
        if path == "/api/history":
            return self.reply(chat.history(q.get("agent", [""])[0]))
        if path == "/api/orders":   # each machine's puller fetches its own queued orders
            return self.reply(chat.pending(q.get("host", [""])[0]))
        self.send_error(404)

    def do_POST(self):
        # same-origin only: a custom header (forces a CORS preflight we never answer) and a matching Origin if sent
        origin = self.headers.get("Origin")
        if self.headers.get("X-AiOps") != "1" or (origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host")):
            return self.send_error(403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
        except ValueError:
            return self.send_error(400)
        try:
            data = json.loads((collect.HERE / "last.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.send_error(503, "no collected data yet; run collect.py first")
        except UnicodeDecodeError:
            return self.send_error(503, "last.json is not readable as UTF-8 text; run collect.py again")
        except OSError as e:   # a permission problem or a folder in its place: answer 503, never crash the handler
            return self.send_error(503, f"last.json could not be read ({e}); run collect.py again")
        except ValueError:
            return self.send_error(503, "last.json is not valid JSON; run collect.py again")
        if not config_ready(collect.CFG):   # a request can arrive after the bind but before the first refresh filled the config
            return self.send_error(503, "serve is still starting: the first refresh has not finished yet")
        cfg = collect.CFG
        text = str(req.get("text", ""))[:4000]
        if self.path == "/api/ask":
            return self.reply(chat.ask(cfg, data, req.get("agent", ""), text))
        if self.path == "/api/order":
            return self.reply(chat.order(cfg, req.get("agent", ""), text))
        if self.path == "/api/orders/ack":
            return self.reply(chat.ack([str(i) for i in req.get("ids", [])]))
        self.send_error(404)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path.startswith("/api/"):
            return self.api_get(path, urllib.parse.parse_qs(qs))
        name = PAGES.get(path)
        f = collect.HERE / name if name else None
        if not f or not f.exists():
            self.send_error(404)
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if name.endswith(".json") else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def refresher(config, every):
    last_ok = None
    while True:
        try:
            collect.collect(config, yes=False)   # hosts without a stored consent are shown as "not allowed yet"
            last_ok = time.strftime("%F %T")
            print(time.strftime("%F %T"), "refreshed", flush=True)
        except Exception:
            traceback.print_exc()
            print(time.strftime("%F %T"), "refresh failed; the last good dashboard stays served",
                  f"(last refresh ok: {last_ok or 'never'}), trying again in {every}s", flush=True)
        time.sleep(every)


def check_config(cfg_path):
    """Parse the config before anything is bound: a broken hosts.json must stop the start, not the refresh."""
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:   # not UTF-8 text (a binary or wrongly saved file): no traceback, nothing bound
        sys.exit(f"{cfg_path} exists but is not readable as UTF-8 text ({e}). Nothing was read or bound. "
                 f"Save it as a plain UTF-8 JSON file like hosts.example.json and start serve.py again.")
    except OSError as e:   # a directory, a permission problem: name the path, bind nothing, print no traceback
        sys.exit(f"{cfg_path} exists but could not be read ({e}). Nothing was read or bound. "
                 f"Fix the path or its permissions (a plain JSON file like hosts.example.json) and start serve.py again.")
    try:
        cfg = json.loads(raw)
    except ValueError as e:
        sys.exit(f"{cfg_path} is not valid JSON ({e}). Nothing was read or bound. Fix that file "
                 f"(or copy hosts.example.json over it) and start serve.py again.")
    if not config_ready(cfg):
        sys.exit(f"{cfg_path} must be a JSON object with a \"hub\" object and a \"hosts\" list, like "
                 f"hosts.example.json. Nothing was read or bound. Fix that file and start serve.py again.")
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--every", type=int, default=600, help="seconds between refreshes")
    ap.add_argument("--config", default=str(collect.CONF_DIR / "hosts.json"))
    a = ap.parse_args()
    cfg_path = collect.Path(a.config)
    if not cfg_path.exists():   # no config: stop before binding, so the port is never touched
        sys.exit(f"{cfg_path} not found and nothing was read or bound. Run collect.py in a terminal on this machine first "
                 f"(cp hosts.example.json to {cfg_path} and answer y per host), then start serve.py again.")
    check_config(cfg_path)      # broken JSON also stops before binding
    try:
        server = http.server.ThreadingHTTPServer((a.bind, a.port), Handler)   # bind only after the config check
    except OSError as e:
        sys.exit(f"{a.bind}:{a.port} is already in use ({e.strerror}). Nothing was bound and nothing was read. "
                 f"Pick a free port: python3 serve.py --port <free port>, then use that same number in "
                 f"tailscale serve --bg --https=<free port> http://127.0.0.1:<free port>, because a tailscale "
                 f"line pointed at a busy port publishes whatever else is listening there.")
    print(time.strftime("%F %T"), "serving", f"http://{a.bind}:{a.port}", flush=True)
    threading.Thread(target=refresher, args=(cfg_path, a.every), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
