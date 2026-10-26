#!/usr/bin/env python3
"""Draft a hosts.json from what this machine already knows: Host entries in ~/.ssh/config and online Linux/macOS
peers in `tailscale status --json`. Read-only; it never connects anywhere. The user decides which to keep.

    python3 init_hosts.py            # print the draft
    python3 init_hosts.py --write    # save to ~/.config/ai-ops-dashboard/hosts.json if it does not exist yet
"""
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

COLORS = ["#2f7de1", "#1c9c6b", "#d9822b", "#c2417e", "#7a5af8", "#0e9aa7", "#8a6d3b"]


def ssh_hosts():
    f = Path.home() / ".ssh/config"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*Host\s+(.+)", line, re.I)
        if m:
            out += [h for h in m.group(1).split() if not re.search(r"[*?!]", h)]
    return out


def tailnet():
    try:
        st = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=15).stdout)
    except Exception:
        return []
    peers = (st.get("Peer") or {}).values()
    return [{"name": p.get("HostName"), "addr": (p.get("TailscaleIPs") or [""])[0], "os": p.get("OS")}
            for p in peers if p.get("Online") and p.get("OS") in ("linux", "macOS")]


def main():
    hosts = []
    seen = set()
    for i, alias in enumerate(ssh_hosts()):
        hosts.append({"name": alias, "ssh": [alias], "color": COLORS[len(hosts) % len(COLORS)], "_from": "~/.ssh/config"})
        seen.add(alias.lower())
    for p in tailnet():
        if p["name"] and p["name"].lower() not in seen:
            hosts.append({"name": p["name"], "ssh": [p["addr"]], "color": COLORS[len(hosts) % len(COLORS)],
                          "_from": f"tailscale ({p['os']})"})
    draft = {"hub": {"name": socket.gethostname().split(".")[0], "subtitle": "", "color": "#6b5bd6",
                     "launchd_match": [], "uses": []},
             "hosts": [{"name": "this machine", "ssh": [], "color": "#6b5bd6"}] + hosts,
             "notes": {}, "events": [], "agents": []}
    text = json.dumps(draft, ensure_ascii=False, indent=1)
    if "--write" in sys.argv:
        dst = Path.home() / ".config/ai-ops-dashboard/hosts.json"
        if dst.exists():
            sys.exit(f"{dst} already exists; not overwriting")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)
        print("wrote", dst)
    else:
        print(text)
    print(f"# {len(hosts)} candidate machines. Remove the ones that should not be read; '_from' keys are ignored.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
