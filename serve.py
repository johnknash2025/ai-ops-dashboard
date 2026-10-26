#!/usr/bin/env python3
"""Serve the dashboard from an always-on machine and refresh it every few minutes.

    python3 serve.py [--bind 127.0.0.1] [--port 8795] [--every 600]

Binds to 127.0.0.1 by default: put it on your tailnet with `tailscale serve` (HTTPS, tailnet only) instead of
opening a port. Only the two generated pages are served; nothing else in this folder is reachable.
Consent is not asked here: run collect.py once in a terminal on this machine and answer y for each host.
"""
import argparse
import http.server
import threading
import time
import traceback

import collect

PAGES = {"/": "dashboard.html", "/dashboard.html": "dashboard.html", "/network.html": "network.html"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        name = PAGES.get(self.path.split("?")[0])
        f = collect.HERE / name if name else None
        if not f or not f.exists():
            self.send_error(404)
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
