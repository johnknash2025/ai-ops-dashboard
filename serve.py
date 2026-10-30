#!/usr/bin/env python3
"""Serve the dashboard from an always-on machine and refresh it every few minutes.

    python3.12 serve.py [--bind 127.0.0.1] [--port 8795] [--every 600]

Binds to 127.0.0.1 by default: put it on your tailnet with `tailscale serve` (HTTPS, tailnet only) instead of
opening a port. Only the two generated pages are served; nothing else in this folder is reachable.
Consent is not asked here: run collect.py once in a terminal on this machine and answer y for each host.
"""
import argparse
import http.server
import threading
import time
import traceback

import json
import urllib.parse

import chat
import collect

PAGES = {"/": "dashboard.html", "/dashboard.html": "dashboard.html", "/network.html": "network.html",
         "/inventory.json": "last.json"}   # the same data as the page, for other agents on the tailnet


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
        cfg, data = collect.CFG, json.loads((collect.HERE / "last.json").read_text())
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
    while True:
        try:
            collect.collect(config, yes=False)   # hosts without a stored consent are shown as "not allowed yet"
            print(time.strftime("%F %T"), "refreshed", flush=True)
        except Exception:
            traceback.print_exc()
        time.sleep(every)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--every", type=int, default=600, help="seconds between refreshes")
    ap.add_argument("--config", default=str(collect.CONF_DIR / "hosts.json"))
    a = ap.parse_args()
    threading.Thread(target=refresher, args=(collect.Path(a.config), a.every), daemon=True).start()
    http.server.ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
