#!/usr/bin/env python3
"""Runs ON each Linux machine (sent over ssh stdin). Prints one JSON object: LLM servers, timers, services, cron.
Read-only: it only lists things and reads unit files / scripts to guess which LLM endpoint a job uses."""
import json
import os
import re
import socket
import subprocess
import urllib.request

PORTS = [8080, 8102, 8103, 1234, 11434, 8000, 8790, 8793, 8646, 4000, 12393]
NOISE = re.compile(r"^(dbus|pipewire|pulseaudio|gvfs|evolution|gnome|xdg|at-spi|dconf|filter-chain|snap\.|gpg-agent|gcr|"
                   r"wireplumber|tracker|ubuntu-|launchpadlib|systemd-|session-|mpris|obex|speech|ibus|org\.)")


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def parse_cpu(lscpu_out="", cpuinfo="", devicetree="", arch=""):
    """CPU name from lscpu, /proc/cpuinfo, or the device tree; never return an empty label."""
    fields = {}
    for text in (lscpu_out, cpuinfo):
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            value = value.replace("\x00", "").strip()
            if key and value and key.lower() not in fields:
                fields[key.lower()] = value
    for key in ("model name", "cpu model", "hardware", "processor", "model"):
        value = fields.get(key, "")
        if value and not value.isdigit():   # lscpu's Model: 0 is a revision, not a name
            return value
    tree = devicetree.replace("\x00", "").strip().splitlines()
    if tree and tree[0].strip():
        return tree[0].strip()
    return f"{arch} CPU" if arch else "不明な CPU"


def cpu_name():
    return parse_cpu(
        sh("lscpu"),
        sh("cat /proc/cpuinfo 2>/dev/null"),
        sh("cat /sys/firmware/devicetree/base/model 2>/dev/null || cat /proc/device-tree/model 2>/dev/null"),
        sh("uname -m").strip(),
    )


def get(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def llm_servers():
    out = []
    for p in PORTS:
        s = socket.socket()
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", p)) != 0:
            s.close()
            continue
        s.close()
        models = []
        d = get(f"http://127.0.0.1:{p}/v1/models")
        if d and isinstance(d.get("data"), list):
            models = [m.get("id") for m in d["data"] if m.get("id")]
        if p == 11434:
            ps = get("http://127.0.0.1:11434/api/ps") or {}
            models = [m.get("name") for m in ps.get("models", [])] or ["(Ollama: none loaded now)"]
        if models:
            pid = sh(f"ss -ltnpH 'sport = :{p}' 2>/dev/null | grep -o 'users:((\"[^\"]*' | head -1").split('"')[-1].strip()
            out.append({"port": p, "models": models, "process": pid})
    return out


def unit_info(name, user=True):
    flag = "--user " if user else ""
    raw = sh(f"systemctl {flag}show {name} -p Description,ExecStart,WorkingDirectory,Result,ActiveState,"
             f"ExecMainExitTimestamp,ExecMainStatus")
    info = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    m = re.search(r"argv\[\]=([^;]+)", info.get("ExecStart", ""))
    info["cmd"] = m.group(1).strip() if m else ""
    return info


def guess_llm(info):
    """Look for an LLM endpoint in the command line and in the scripts it runs."""
    text = info.get("cmd", "")
    for tok in text.split():
        if tok.startswith("/") and os.path.isfile(tok) and os.path.getsize(tok) < 400_000:
            try:
                text += "\n" + open(tok, errors="ignore").read()
            except OSError:
                pass
    wd = info.get("WorkingDirectory", "")
    for f in ("config.json", "config.yaml", "settings.json"):  # never .env: it holds secrets
        p = os.path.join(wd, f) if wd else ""
        if p and os.path.isfile(p) and os.path.getsize(p) < 200_000:
            try:
                text += "\n" + open(p, errors="ignore").read()
            except OSError:
                pass
    hits = sorted(set(re.findall(r"(?:127\.0\.0\.1|localhost|[0-9a-z.-]+\.ts\.net|100\.\d+\.\d+\.\d+):(\d{4,5})", text)))
    names = sorted(set(re.findall(r"(deepseek[\w.-]*|qwen[\w.-]*|glm[\w.-]*|gemma[\w.-]*|llama[\w.-]*|minimax[\w.-]*|gpt-oss[\w.-]*)",
                                  text, re.I)))[:3]
    return {"ports": [int(h) for h in hits if 1000 < int(h) < 65000][:4], "model_hints": names}


def timers():
    res = []
    for line in sh("systemctl --user list-timers --all --no-legend 2>/dev/null").splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[-1].endswith(".service"):
            continue
        svc, tmr = parts[-1], parts[-2]
        if NOISE.match(svc):
            continue
        nxt = " ".join(parts[:4]) if parts[0] != "n/a" else "n/a"
        last = sh(f"systemctl --user show {tmr} -p LastTriggerUSec --value").strip().removesuffix(" JST") or "n/a"
        info = unit_info(svc)
        sched = sh(f"systemctl --user show {tmr} -p TimersCalendar,TimersMonotonic --value").replace("\n", " ").strip()
        res.append({"unit": svc, "description": info.get("Description", ""), "next": nxt, "last": last,
                    "result": info.get("Result", ""), "exit": info.get("ExecMainStatus", ""),
                    "schedule": re.sub(r"\s+", " ", sched)[:600], "llm": guess_llm(info)})
    return res


def services():
    res = []
    for scope, user in (("user", True), ("system", False)):
        flag = "--user " if user else ""
        for line in sh(f"systemctl {flag}list-units --type=service --state=running --no-legend --plain 2>/dev/null").splitlines():
            name = line.split()[0] if line.split() else ""
            if not name or NOISE.match(name):
                continue
            if not user:
                path = sh(f"systemctl show {name} -p FragmentPath --value").strip()
                if not path.startswith("/etc/systemd/system/"):
                    continue   # only units the owner added, not the distribution's
            info = unit_info(name, user)
            res.append({"unit": name, "scope": scope, "description": info.get("Description", ""), "llm": guess_llm(info)})
    return res


def gpu():
    q = sh("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null").strip()
    if q:
        n, used, total, util = [x.strip() for x in q.split(",")[:4]]
        return f"{n}: {used}/{total} MiB, {util}%"
    amd = sh("cat /sys/class/drm/card*/device/mem_info_vram_used 2>/dev/null | head -1").strip()
    return f"AMD GPU VRAM used {int(amd) // 2**20} MiB" if amd.isdigit() else ""


def inbox_events():
    """Recent events taken from a webhook inbox (agent-inbox writes ~/agent-inbox/events/<date>.md), newest last."""
    d = os.path.expanduser("~/agent-inbox/events")
    if not os.path.isdir(d):
        return None
    out = []
    for f in sorted(os.listdir(d))[-2:]:
        for line in open(os.path.join(d, f), errors="ignore"):
            m = re.match(r"- (\d\d:\d\d) (\S+) `([^`]+)` ?(\S*)", line)
            if m:
                out.append({"date": f[:10], "time": m.group(1), "source": m.group(2), "type": m.group(3), "repo": m.group(4)})
    return out[-50:]


def reports():
    """Agent reports: ~/.ai-ops/reports/*.json, each {"time", "source", "level", "title", "summary", "link"}.
    Any agent can drop one there; only the newest 30 are read, and only these fields."""
    d = os.path.expanduser("~/.ai-ops/reports")
    if not os.path.isdir(d):
        return None
    out = []
    for f in sorted(os.listdir(d))[-30:]:
        try:
            r = json.load(open(os.path.join(d, f)))
            out.append({k: str(r.get(k, ""))[:600] for k in ("time", "source", "level", "title", "summary", "link")})
        except Exception:
            pass
    return out


def inventory():
    """Hardware, OS, storage, versions of common tools, and git checkouts in the home folder (remote, tag, last commit)."""
    os_rel = dict(l.split("=", 1) for l in sh("cat /etc/os-release").splitlines() if "=" in l)
    arch = sh("uname -m").strip()
    disks = []
    for line in sh("df -B1 -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs --output=source,fstype,size,used,target 2>/dev/null").splitlines()[1:]:
        p = line.split()
        if len(p) >= 5 and int(p[2]) > 20 * 2**30 and not (p[1] == "zfs" and int(p[3]) < 10 * 2**30):
            disks.append({"dev": p[0], "fs": p[1], "size_gb": int(p[2]) // 2**30, "used_gb": int(p[3]) // 2**30, "mount": p[4]})
    pools = [l.split("\t") for l in sh("zpool list -H -o name,size,alloc,health 2>/dev/null").splitlines() if l]
    tools = {}
    for name, cmd in [("tailscale", "tailscale version | head -1"), ("docker", "docker --version"), ("python3", "python3 --version"),
                      ("nvidia_driver", "nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1"),
                      ("cuda", "nvcc --version | grep -o 'release [0-9.]*'"), ("ollama", "ollama --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1"),
                      ("node", "node -v"), ("git", "git --version"), ("openssh", "ssh -V 2>&1")]:
        v = sh(cmd).strip()
        if v:
            tools[name] = v.split("\n")[0][:60]
    repos = []
    for d in sorted(os.listdir(os.path.expanduser("~"))):
        g = os.path.expanduser(f"~/{d}")
        if os.path.isdir(os.path.join(g, ".git")):
            url = sh(f"git -C '{g}' remote get-url origin 2>/dev/null").strip()
            url = re.sub(r"https://[^@/]+@", "https://", url)            # never keep credentials embedded in a URL
            repos.append({"dir": d, "remote": url, "tag": sh(f"git -C '{g}' describe --tags --always 2>/dev/null").strip()[:40],
                          "last_commit": sh(f"git -C '{g}' log -1 --format=%cs 2>/dev/null").strip()})
    return {"os": os_rel.get("PRETTY_NAME", "").strip('"'), "kernel": sh("uname -r").strip(), "arch": arch,
            "cpu": cpu_name(), "cores": os.cpu_count(),
            "ram_gb": round(int(sh("awk '/MemTotal/{print $2}' /proc/meminfo") or 0) / 2**20),
            "disks": disks, "zpools": [{"name": p[0], "size": p[1], "alloc": p[2], "health": p[3]} for p in pools if len(p) >= 4],
            "tools": tools, "repos": repos[:40]}


def agent_data():
    """Structured data other agents publish for the dashboard: ~/.ai-ops/data/<name>.json (only the newest 10, max 200 KB each)."""
    d = os.path.expanduser("~/.ai-ops/data")
    if not os.path.isdir(d):
        return None
    out = {}
    for f in sorted(os.listdir(d))[:10]:
        p = os.path.join(d, f)
        if f.endswith(".json") and os.path.getsize(p) < 200_000:
            try:
                out[f[:-5]] = json.load(open(p))
            except Exception:
                pass
    return out


RANK = ["local", "tailnet", "lan", "all"]


def network():
    """Addresses and who can reach each listening TCP port: this machine only, the tailnet, the LAN, or every network."""
    addrs = []
    for line in sh("ip -4 -br addr 2>/dev/null").splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] != "lo" and not p[0].startswith(("docker", "br-", "veth", "virbr", "mpqemu", "lxd")):
            addrs.append({"iface": p[0], "addr": p[2].split("/")[0]})
    ports = {}
    for line in sh("ss -ltnpH 2>/dev/null").splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        host, _, port = p[3].rpartition(":")
        host = host.strip("[]").split("%")[0]
        scope = ("local" if host.startswith("127.") or host == "::1" else
                 "tailnet" if host.startswith("100.") or host.startswith("fd7a:") else
                 "all" if host in ("0.0.0.0", "::", "*") else "lan")
        proc = re.search(r'users:\(\("([^"]+)', line)
        prev = ports.get(port)
        if not prev or RANK.index(scope) > RANK.index(prev["scope"]):
            ports[port] = {"port": int(port), "scope": scope, "process": proc.group(1) if proc else ""}
    return {"addrs": addrs, "listen": sorted(ports.values(), key=lambda x: x["port"])}


def main():
    print(json.dumps({
        "host": socket.gethostname(), "uptime": sh("uptime -p").strip(), "gpu": gpu(),
        "mem": sh("free -g | awk '/Mem:/{print $3\"/\"$2\" GiB used\"}'").strip(),
        "llm_servers": llm_servers(), "net": network(), "events": inbox_events(), "reports": reports(),
        "inventory": inventory(), "data": agent_data(), "timers": timers(), "services": services(),
        "cron": [l for l in sh("crontab -l 2>/dev/null").splitlines() if l.strip() and not l.startswith("#")],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
