#!/usr/bin/env python3
"""Push mode: machines the dashboard cannot (or should not) log in to send their own snapshot instead.

Runs on a Mac (launchd, every 10 minutes): takes a snapshot of this Mac and, optionally, of a QNAP NAS reached over
the Mac's ssh, and writes them to the serving machine as ~/.ai-ops/push/<name>.json. The Mac keeps Remote Login off,
the NAS needs no Python, and the serving machine holds no key to either. Read-only, like remote_probe.py.

    python3 push.py --to gx10 [--nas nas] [--name mac]     (ssh aliases)
"""
import argparse
import json
import os
import plistlib
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

PORTS = [8080, 8102, 8103, 1234, 11434, 8000, 8790, 8793, 18080, 18081, 18082]


def sh(cmd, timeout=60):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def models(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as r:
            return [m.get("id") for m in json.load(r).get("data", []) if m.get("id")]
    except Exception:
        return []


def schedule_of(pl):
    cal = pl.get("StartCalendarInterval")
    if isinstance(cal, dict):
        cal = [cal]
    if cal:
        return " ".join(f"OnCalendar=*-*-* {c.get('Hour', '*')}:{c.get('Minute', 0)}:00" for c in cal)
    if pl.get("StartInterval"):
        return f"OnUnitActiveUSec={pl['StartInterval']}s"
    return ""


def mac_snapshot():
    running = {}
    for line in sh("launchctl list").splitlines()[1:]:
        p = line.split("\t")
        if len(p) == 3:
            running[p[2]] = (p[0], p[1])
    timers, services = [], []
    for f in sorted((Path.home() / "Library/LaunchAgents").glob("*.plist")):
        try:
            pl = plistlib.loads(f.read_bytes())
        except Exception:
            continue
        label = pl.get("Label", f.stem)
        if label.startswith(("com.apple.", "com.google.", "com.microsoft.")):
            continue
        pid, status = running.get(label, ("-", ""))
        sched = schedule_of(pl)
        if sched:
            timers.append({"unit": label, "description": label, "schedule": sched, "last": "n/a",
                           "result": "success" if status in ("0", "") else f"exit {status}", "llm": {"ports": [], "model_hints": []}})
        elif pid != "-":
            services.append({"unit": label, "scope": "user", "description": label, "llm": {"ports": [], "model_hints": []}})
    llm = []
    for p in PORTS:
        s = socket.socket()
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", p)) == 0:
            ms = models(p)
            if ms:
                llm.append({"port": p, "models": ms, "process": "tunnel" if p >= 18000 else ""})
        s.close()
    listen = []
    for line in sh("lsof -nP -iTCP -sTCP:LISTEN").splitlines()[1:]:
        p = line.split()
        if len(p) >= 9:
            host, _, port = p[8].rpartition(":")
            scope = "local" if host.startswith(("127.", "[::1]", "localhost")) else "all" if host in ("*", "[::]") else "lan"
            listen.append({"port": int(port) if port.isdigit() else 0, "scope": scope, "process": p[0]})
    uniq = {}
    for x in listen:
        uniq.setdefault(x["port"], x)
    disks = []
    for line in sh("df -k -l").splitlines()[1:]:
        p = line.split()
        if len(p) >= 9 and p[8] in ("/", "/System/Volumes/Data") and int(p[1]) > 20 * 2**20:
            disks.append({"dev": p[0], "fs": "apfs", "size_gb": int(p[1]) // 2**20, "used_gb": (int(p[1]) - int(p[3])) // 2**20, "mount": p[8]})
    tools = {k: sh(c).strip().split("\n")[0][:60] for k, c in [
        ("tailscale", "tailscale version | head -1"), ("docker", "docker --version"), ("python3", "python3 --version"),
        ("node", "node -v"), ("git", "git --version"), ("openssh", "ssh -V 2>&1")] if sh(c).strip()}
    repos = []
    for d in sorted((Path.home() / "work").glob("*/.git"))[:40]:
        g = d.parent
        url = re.sub(r"https://[^@/]+@", "https://", sh(f"git -C '{g}' remote get-url origin 2>/dev/null").strip())
        repos.append({"dir": f"work/{g.name}", "remote": url, "tag": sh(f"git -C '{g}' describe --tags --always 2>/dev/null").strip()[:40],
                      "last_commit": sh(f"git -C '{g}' log -1 --format=%cs 2>/dev/null").strip()})
    fw = "on" if "enabled" in sh("/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate") else "off"
    return {"host": socket.gethostname().split(".")[0], "uptime": sh("uptime").strip()[:80],
            "gpu": sh("sysctl -n machdep.cpu.brand_string").strip() + " (統合 GPU)",
            "mem": f"{int(sh('sysctl -n hw.memsize') or 0) // 2**30} GiB", "llm_servers": llm, "timers": timers, "services": services,
            "cron": [l for l in sh("crontab -l 2>/dev/null").splitlines() if l.strip() and not l.startswith("#")],
            "net": {"addrs": [{"iface": "tailscale0", "addr": sh("tailscale ip -4").strip()}] if sh("tailscale ip -4").strip() else [],
                    "listen": sorted(uniq.values(), key=lambda x: x["port"])},
            "events": None, "reports": None,
            "inventory": {"os": f"macOS {sh('sw_vers -productVersion').strip()}", "kernel": sh("uname -r").strip(), "arch": sh("uname -m").strip(),
                          "cpu": sh("sysctl -n machdep.cpu.brand_string").strip(), "cores": os.cpu_count(),
                          "ram_gb": int(sh("sysctl -n hw.memsize") or 0) // 2**30, "disks": disks, "zpools": [], "tools": tools, "repos": repos,
                          "security": {"firewall": fw, "filevault": "on" if "On" in sh("fdesetup status") else "off",
                                       "remote_login": "on" if "On" in sh("systemsetup -getremotelogin 2>/dev/null") else "off/unknown"}}}


NAS_SCRIPT = r"""
g() { getcfg "$@" 2>/dev/null; }
D=$(command -v docker || ls /share/*/.qpkg/container-station/bin/docker 2>/dev/null | head -1)
echo "@@os"; echo "QTS $(g System Version) build $(g System 'Build Number') $(g System Model)"; uname -r; hostname
echo "@@uptime"; uptime
echo "@@mem"; awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo; grep -c ^processor /proc/cpuinfo; grep -m1 'model name' /proc/cpuinfo | cut -d: -f2
echo "@@df"; df -kP | awk '/CACHEDEV[0-9]_DATA$/{print $2, $3, $6}'
echo "@@raid"; awk '/^md[0-9]+ :/{n=$1; l=$4} /blocks/ && n{print n, l, $NF; n=""}' /proc/mdstat
echo "@@listen"; netstat -tln 2>/dev/null | awk 'NR>2{print $4}'
echo "@@containers"; $D ps --format '{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}' 2>/dev/null
echo "@@qpkg"; for s in $(awk -F'[][]' '/^\[/{print $2}' /etc/config/qpkg.conf); do echo "$s|$(g "$s" Version -f /etc/config/qpkg.conf)|$(g "$s" Enable -f /etc/config/qpkg.conf)"; done
echo "@@sec"
echo "upnp_router=$(g UPnP_Global Enable -f /etc/config/upnpc.conf)"
echo "myqnapcloud_publish=$(grep -cE '^Service[0-9]+ = 1' /etc/config/qnapddns.conf 2>/dev/null)"
echo "live_update=$(g System 'Enable Live Update')"; echo "last_update_check=$(g System 'Latest Check Live Update')"
echo "smb=$(g Samba Enable) nfs=$(g NFS Enable) ftp=$(g FTP Enable) snmp=$(g SNMP 'Server Enable')"
echo "ssh=$(g LOGIN 'SSH Enable') telnet=$(g LOGIN 'TELNET Enable') web_port=$(g System 'Web Access Port') https=$(g Stunnel Enable)"
echo "admin_disabled=$(grep -c '^admin:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:/bin/false' /etc/passwd)"
echo "login_failures_recent=$(tail -500 /etc/logs/event.log 2>/dev/null | grep -ciE 'failed to log in|login fail')"
echo "malware_events_recent=$(tail -500 /etc/logs/event.log 2>/dev/null | grep -ciE 'malware|ransom')"
echo "@@minio"
src=$($D inspect minio --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)
echo "image=$($D inspect minio --format '{{.Config.Image}}' 2>/dev/null)"
echo "src=$src"
if [ -n "$src" ] && [ -d "$src" ]; then for b in "$src"/*; do [ -d "$b" ] || continue; n=$(basename "$b"); echo "bucket|$n|$(du -sk "$b" 2>/dev/null | cut -f1)|$(ls "$b" 2>/dev/null | head -6 | tr '\n' ',')|$(ls -t "$b" 2>/dev/null | head -1)"; done; fi
echo "@@end"
"""


def nas_snapshot(alias):
    raw = sh(f"ssh -o ConnectTimeout=10 -o BatchMode=yes {alias} sh -s <<'EOF'\n{NAS_SCRIPT}\nEOF", timeout=600)
    sec, cur = {}, None
    for line in raw.splitlines():
        if line.startswith("@@"):
            cur = line[2:]
            sec[cur] = []
        elif cur:
            sec[cur].append(line)
    if "os" not in sec:
        return {"error": "NAS に届かない（MacBook Pro から ssh できない）"}
    osl = sec["os"] + ["", "", ""]
    mem = sec.get("mem", []) + ["0", "0", ""]
    disks = [{"dev": "", "fs": "ext4", "size_gb": int(a) // 2**20, "used_gb": int(b) // 2**20, "mount": m}
             for a, b, m in (l.split() for l in sec.get("df", []) if len(l.split()) == 3)]
    listen = []
    for a in sec.get("listen", []):
        host, _, port = a.rpartition(":")
        if port.isdigit():
            listen.append({"port": int(port), "scope": "local" if host.startswith("127.") or host == "::1" else "all", "process": ""})
    uniq = {}
    for x in listen:
        if x["port"] not in uniq or x["scope"] == "all":
            uniq[x["port"]] = x
    containers = [dict(zip(["name", "image", "ports", "status"], l.split("|"))) for l in sec.get("containers", []) if "|" in l]
    s = {}
    for l in sec.get("sec", []):
        for kv in l.split():
            if "=" in kv:
                k, v = kv.split("=", 1)
                s[k] = v
    mi = {"image": "", "buckets": []}
    for l in sec.get("minio", []):
        if l.startswith("image="):
            mi["image"] = l[6:]
        elif l.startswith("bucket|"):
            _, name, kb, prefixes, newest = (l.split("|") + [""] * 5)[:5]
            mi["buckets"].append({"name": name, "size_gb": round(int(kb or 0) / 2**20, 1),
                                  "prefixes": [p for p in prefixes.split(",") if p][:6], "newest": newest})
    return {"host": osl[2] or "nas", "uptime": " ".join(sec.get("uptime", [""]))[:80], "gpu": "", "mem": f"{mem[0]} GiB",
            "llm_servers": [], "timers": [], "cron": [],
            "services": [{"unit": f"docker:{c['name']}", "scope": "container", "description": f"{c['image']} {c['ports']}",
                          "llm": {"ports": [], "model_hints": []}} for c in containers],
            "net": {"addrs": [], "listen": sorted(uniq.values(), key=lambda x: x["port"])}, "events": None, "reports": None,
            "inventory": {"os": osl[0], "kernel": osl[1], "arch": "", "cpu": mem[2].strip(), "cores": int(mem[1] or 0),
                          "ram_gb": int(mem[0] or 0), "disks": disks, "zpools": [],
                          "raid": sec.get("raid", []), "tools": {"docker_containers": f"{len(containers)} 個"},
                          "apps": [dict(zip(["name", "version", "enabled"], l.split("|"))) for l in sec.get("qpkg", []) if "|" in l],
                          "containers": containers, "repos": [], "security": s},
            "data": {"minio": mi}}


def push(to, name, snap):
    snap["pushed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", to,
                    f"mkdir -p ~/.ai-ops/push && cat > ~/.ai-ops/push/{name}.json.tmp && mv ~/.ai-ops/push/{name}.json.tmp ~/.ai-ops/push/{name}.json"],
                   input=json.dumps(snap, ensure_ascii=False), text=True, check=True, timeout=60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="ssh alias of the serving machine")
    ap.add_argument("--name", default="mac")
    ap.add_argument("--nas", help="ssh alias of a QNAP NAS to include")
    a = ap.parse_args()
    push(a.to, a.name, mac_snapshot())
    stamp = Path.home() / ".ai-ops/nas.stamp"   # the NAS walk (bucket sizes) is heavy: at most once an hour
    if a.nas and (not stamp.exists() or time.time() - stamp.stat().st_mtime > 3600):
        push(a.to, "nas", nas_snapshot(a.nas))
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    print("pushed")


if __name__ == "__main__":
    main()
