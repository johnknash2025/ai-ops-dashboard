#!/usr/bin/env python3
"""Collect who-does-what from every machine and write dashboard.html (local file, summary only, no secrets)."""
import argparse
import hashlib
import html
import os
import plistlib
import re
import sys
import json
import socket
import subprocess
import time

import assets
import org
from pathlib import Path

HERE = Path(__file__).parent
PROBE = (HERE / "remote_probe.py").read_text()
CONF_DIR = Path(os.environ.get("AI_OPS_CONFIG_DIR", Path.home() / ".config/ai-ops-dashboard"))
CONSENT = CONF_DIR / "consent.json"
DISCLOSURE = """Before reading anything on "{name}" ({how}) this tool needs your OK. It will, read-only:
  - list LLM servers answering on 127.0.0.1 (model names from /v1/models, Ollama /api/ps)
  - list systemd user timers, running user services, services you added under /etc/systemd/system
  - read your crontab lines
  - read the scripts those units start (and config.json / config.yaml / settings.json in their working
    directory) only to pick out port numbers and model names; the file contents are not stored
  - read GPU / RAM usage and uptime
  - read the newest agent reports in ~/.ai-ops/reports and webhook events in ~/agent-inbox/events (if present)
  - list its network addresses and listening TCP ports with the process name (who can reach each port)
It changes nothing, sends nothing anywhere, and the result stays in {out} on this computer.
The consent is remembered in {consent} and asked again whenever the probe script changes."""
CFG, COLORS, NOTES = {}, {}, {}
HUB_HOST = socket.gethostname().split(".")[0]


def load_config(path):
    if not path.exists():
        sys.exit(f"No config at {path}. Copy hosts.example.json there and edit it (see README).")
    c = json.loads(path.read_text())
    CFG.update(c)
    COLORS.update({h["name"]: h.get("color", "#888") for h in c["hosts"]})
    COLORS[c["hub"]["name"]] = c["hub"].get("color", "#6b5bd6")
    NOTES.update(c.get("notes", {}))
    return c


def consented(name, how, yes):
    """Ask once per machine (and again when the probe or the connection changes); remember the answer."""
    digest = hashlib.sha256((PROBE + how).encode()).hexdigest()
    store = json.loads(CONSENT.read_text()) if CONSENT.exists() else {}
    if store.get(name, {}).get("sha256") == digest:
        return True
    print(DISCLOSURE.format(name=name, how=how, out=HERE / "dashboard.html", consent=CONSENT))
    if yes:
        print("  -> accepted with --yes")
    elif not sys.stdin.isatty() or input("Allow? [y/N] ").strip().lower() != "y":
        return False
    store[name] = {"sha256": digest, "at": time.strftime("%F %T")}
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONSENT.write_text(json.dumps(store, indent=1))
    return True


def probe(name, ssh, yes, installed=False):
    """ssh [] = this machine. installed = the target's authorized_keys runs remote_probe.py as a forced command,
    so the key can do nothing else and nothing is sent to run."""
    how = ("ssh " + " ".join(ssh) + (" (installed probe)" if installed else "")) if ssh else "this machine"
    if not consented(name, how, yes):
        return {"error": "not allowed yet: run collect.py in a terminal and answer y"}
    if not ssh:
        cmd = [sys.executable, "-"]
    elif installed:
        cmd = ["ssh", "-T", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", *ssh]
    else:
        cmd = ["ssh", "-o", "ConnectTimeout=8", *ssh, "python3 -"]
    try:
        r = subprocess.run(cmd, input="" if installed else PROBE, capture_output=True, text=True, timeout=180)
        return json.loads(r.stdout) if r.stdout.strip() else {"error": (r.stderr or "no output").strip()[:200]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def hub():
    h = CFG["hub"]
    pat = "|".join(re.escape(x) for x in h.get("launchd_match", [])) or "^$"
    agents = []
    if sys.platform == "darwin":
        for line in subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.splitlines()[1:]:
            if re.search(pat, line):
                agents.append(line.split())
    return {"agents": agents, "uses": h.get("uses", []), "tunnels": tunnels(h.get("launchd_match", []))}


def tunnels(match):
    """ssh -L forwards started by the hub's LaunchAgents: local port -> port on the far machine."""
    out = []
    for f in sorted((Path.home() / "Library/LaunchAgents").glob("*.plist")):
        if not any(m in f.name for m in match):
            continue
        try:
            args = plistlib.loads(f.read_bytes()).get("ProgramArguments", [])
        except Exception:
            continue
        if not args or not args[0].endswith("ssh"):
            continue
        for i, a in enumerate(args):
            if a == "-L" and i + 1 < len(args):
                parts = args[i + 1].split(":")
                out.append({"agent": f.stem, "local": parts[-3] if len(parts) >= 3 else "?",
                            "remote": parts[-1], "target": args[-1]})
    return out


def short(name, n=22):
    return name if len(name) <= n else name[:n - 1] + "…"


def hostname_of(h, data):
    """Show the machine's own hostname; the config name becomes the nickname."""
    return data.get(h, {}).get("host") or h


def expand(field, top):
    """One systemd calendar field (hour or minute): *, a,b, a..b, a/step -> sorted values."""
    vals = set()
    for part in field.split(","):
        base, _, step = part.partition("/")
        step = int(step) if step else 1
        if base == "*":
            lo, hi = 0, top - 1
        elif ".." in base:
            lo, hi = (int(x) for x in base.split(".."))
        else:
            lo = int(base)
            hi = top - 1 if step > 1 else lo
        vals.update(range(lo, hi + 1, step))
    return sorted(vals)


def run_times(schedule):
    """Hours of the day (0-24, fractional) a timer fires, plus a short phrase. Interval timers return (None, phrase)."""
    times = []
    for hh, mm in re.findall(r"OnCalendar=\S+ ([\d*,./]+):([\d*,./]+):\S+", schedule):
        try:
            times += [h + m / 60 for h in expand(hh, 24) for m in expand(mm, 60)]
        except ValueError:
            pass
    every = re.search(r"OnUnitActiveUSec=(\w+)", schedule)
    if every and not times:
        e = every.group(1).replace("min", "分").replace("h", "時間").replace("s", "秒")
        return None, f"{e}ごと"
    times = sorted(set(times))
    if not times:
        return [], "（不明）"
    if len(times) == 1:
        h = times[0]
        return times, f"毎日 {int(h)}:{round(h % 1 * 60):02d}"
    if len(times) > 12:
        return times, f"1日 {len(times)} 回"
    return times, " · ".join(f"{int(h)}:{round(h % 1 * 60):02d}" for h in times)


def cron_schedule(line):
    """'7,37 * * * * cmd' -> ('OnCalendar=*-*-* H:M:00 ...' text that run_times() understands, command)."""
    f = line.split(None, 5)
    if len(f) < 6 or f[0].startswith("@"):
        return None, line
    mi, hr, dom, mon, dow, cmd = f
    if (dom, mon, dow) != ("*", "*", "*"):
        return f"（{dom} {mon} {dow} のみ）", cmd
    conv = lambda x: x.replace("*/", "0/")
    return f"OnCalendar=*-*-* {conv(hr)}:{conv(mi)}:00", cmd


def cron_name(cmd):
    """A short name for a cron command: the script's file name without the extension."""
    m = re.findall(r"[\w.-]+\.(?:py|sh|js)", cmd)
    return m[-1].rsplit(".", 1)[0] if m else cmd.split()[0].rsplit("/", 1)[-1]


def strip(schedule, color):
    """A 24-hour band: a mark at every run time, a shaded band for interval jobs, and a line for now."""
    times, phrase = run_times(schedule)
    w, hgt = 168, 16
    now = time.localtime()
    nx = 4 + (now.tm_hour + now.tm_min / 60) / 24 * (w - 8)
    if times is None:
        marks = f'<rect x="4" y="5" width="{w - 8}" height="6" rx="2" fill="{color}" opacity=".55"/>'
    else:
        marks = "".join(f'<rect x="{4 + t / 24 * (w - 8) - 1:.1f}" y="2" width="2.4" height="12" rx="1" fill="{color}"/>' for t in times)
    grid = "".join(f'<line x1="{4 + k / 24 * (w - 8):.1f}" x2="{4 + k / 24 * (w - 8):.1f}" y1="6" y2="10" stroke="var(--line)"/>' for k in range(0, 25, 6))
    return (f'<svg width="{w}" height="{hgt}" class=strip><line x1="4" x2="{w - 4}" y1="8" y2="8" stroke="var(--line)"/>{grid}{marks}'
            f'<line x1="{nx:.1f}" x2="{nx:.1f}" y1="0" y2="16" stroke="var(--bad)" stroke-width="1.2"/></svg>'
            f'<div class=mute>{esc(phrase)}</div>')


def esc(s):
    return html.escape(str(s))


def note(unit):
    return NOTES.get(unit.removesuffix(".service"), "")


def llm_cell(llm, ports_to_model):
    bits = [f"{p} → {ports_to_model.get(p, '?')}" for p in llm.get("ports", [])]
    if not bits and llm.get("model_hints"):
        bits = ["ヒント: " + ", ".join(llm["model_hints"])]
    return esc(" / ".join(bits)) or '<span class="mute">LLM 不使用（推定）</span>'




def hour(t):
    try:
        return int(t["last"].split()[2][:2]) + int(t["last"].split()[2][3:5]) / 60
    except Exception:
        return None


def overview(data):
    """Tiles, a who-serves-whom diagram and a 24h chart of when the timers last ran."""
    ok = sum(t["result"] == "success" for d in data.values() for t in d.get("timers", []))
    bad = sum(t["result"] != "success" for d in data.values() for t in d.get("timers", []))
    llms = sum(len(d.get("llm_servers", [])) for d in data.values())
    svcs = sum(len(d.get("services", [])) for d in data.values())
    tiles = "".join(f'<a class=tile href="#{v}"><div class=ico>{i}</div><div class=num style="color:{c}">{n}</div>{esc(l)}</a>' for i, n, l, c, v in
                    [("🧠", llms, "LLM サーバー", "var(--fg)", "machines"), ("✅", ok, "定期 成功", "var(--ok)", "jobs"),
                     ("❌" if bad else "✅", bad, "定期 失敗", "var(--bad)" if bad else "var(--ok)", "jobs"),
                     ("⚙️", svcs, "常駐サービス", "var(--fg)", "machines")])
    # diagram: hub on the left, machines in the middle, their models on the right
    hosts = [h for h in data if "error" not in data[h]]
    rows, y = [], 20
    hub_y = 20 + 46 * sum(max(1, len(data[h]["llm_servers"])) for h in hosts) / 2 - 20
    for h in hosts:
        c, models = COLORS.get(h, "#888"), data[h]["llm_servers"] or [{"port": "", "models": ["（なし）"]}]
        my = y + 46 * len(models) / 2 - 20
        n = len(data[h]["timers"])
        rows.append(f'<path d="M190 {hub_y+20} C260 {hub_y+20},250 {my+20},320 {my+20}" stroke="{c}" fill="none" stroke-width="2"/>'
                    f'<rect x="320" y="{my}" width="170" height="40" rx="8" fill="{c}"/>'
                    f'<text x="405" y="{my+18}" class="w">{esc(short(hostname_of(h, data)))}<title>{esc(hostname_of(h, data))}</title></text><text x="405" y="{my+33}" class="ws">{esc(h)} · 定期 {n} 件</text>')
        for s in models:
            label = esc(s["models"][0][:28]) + (f" ほか{len(s['models'])-1}" if len(s["models"]) > 1 else "")
            rows.append(f'<path d="M490 {my+20} C540 {my+20},530 {y+18},570 {y+18}" stroke="{c}" fill="none" stroke-opacity=".5"/>'
                        f'<rect x="570" y="{y}" width="330" height="36" rx="6" fill="none" stroke="{c}"/>'
                        f'<text x="582" y="{y+23}" class="t l">:{s["port"]} {label}</text>')
            y += 46
        y += 8 if len(models) == 1 else 0
    hub = (f'<rect x="20" y="{hub_y}" width="170" height="40" rx="8" fill="{COLORS[CFG["hub"]["name"]]}"/>'
           f'<text x="105" y="{hub_y+18}" class="w">{esc(short(HUB_HOST))}</text><text x="105" y="{hub_y+33}" class="ws">{esc(CFG["hub"]["name"])}</text>')
    diagram = f'<svg viewBox="0 0 920 {y+10}" class=diag>{hub}{"".join(rows)}</svg>'
    # 24h chart
    lane = 34
    dots, labels = [], []
    for i, h in enumerate(hosts):
        cy = 30 + i * lane
        labels.append(f'<text x="80" y="{cy+4}" class="t" text-anchor="end">{esc(short(hostname_of(h, data), 14))}</text>'
                      f'<line x1="90" x2="890" y1="{cy}" y2="{cy}" stroke="var(--line)"/>')
        for t in data[h]["timers"]:
            hr = hour(t)
            if hr is None:
                continue
            fill = COLORS.get(h) if t["result"] == "success" else "var(--bad)"
            dots.append(f'<circle cx="{90 + hr / 24 * 800:.0f}" cy="{cy}" r="6" fill="{fill}">'
                        f'<title>{esc(t["unit"].removesuffix(".service"))} {esc(t["last"])} {esc(t["result"])}</title></circle>')
    ticks = "".join(f'<text x="{90 + k / 24 * 800:.0f}" y="{30 + len(hosts) * lane}" class="t">{k}時</text>' for k in range(0, 25, 3))
    chart = f'<svg viewBox="0 0 920 {40 + len(hosts) * lane}" class=diag>{"".join(labels)}{"".join(dots)}{ticks}</svg>'
    evs = events_section(data)
    home = f'<div class=tiles>{tiles}</div><section><h2>どれが何を受け持つか</h2>{diagram}</section>'
    jobs = f'<section class=periodic><h2>🕒 定期の仕事が最後に動いた時刻 <span class=mute>赤は失敗・点にカーソルで名前</span></h2>{chart}</section>'
    return home, jobs, evs


VIEWS = [("home", "🏠", "概要"), ("org", "🏢", "組織"), ("jobs", "🕒", "定期"), ("events", "⚡", "イベント"), ("reports", "🛡", "報告"),
         ("machines", "🖥", "マシン"), ("assets", "🧰", "資産"), ("roadmap", "🗺", "予定"), ("prices", "💴", "価格"), ("net", "🌐", "ネット")]


def page(title, body, views=None):
    """views: {view_id: html}. One view is shown at a time; the left rail (bottom bar on phones) switches them."""
    if views:
        rail = "".join(f'<a href="#{v}" data-v="{v}"><span>{i}</span><small>{n}</small></a>' for v, i, n in VIEWS if v in views)
        body = "".join(f'<div class=view id="v-{v}">{views[v]}</div>' for v, _, _ in VIEWS if v in views)
    else:
        rail = '<a href="dashboard.html"><span>🏠</span><small>戻る</small></a>'
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>AI 運用ダッシュボード: {title}</title>
<style>:root{{--bg:#f6f7f9;--card:#fff;--fg:#1b1b1b;--mute:#777;--line:#e4e4e4;--ok:#17803d;--bad:#c0392b;--per:#2f7de1;--ev:#b26a00;--evbg:#fff8ec;--rail:#1f2430}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111215;--card:#1a1b1f;--fg:#e8e8e8;--mute:#999;--line:#30323a;--ok:#4cc27a;--bad:#ff7a6b;--per:#5598e7;--ev:#f0a630;--evbg:#221c12;--rail:#0b0c0f}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"Hiragino Sans",sans-serif;margin:0}}
nav.rail{{position:fixed;left:0;top:0;bottom:0;width:76px;background:var(--rail);display:flex;flex-direction:column;align-items:center;padding-top:14px;gap:6px}}
nav.rail a{{color:#cfd3dc;text-decoration:none;display:flex;flex-direction:column;align-items:center;width:64px;padding:8px 0;border-radius:12px}}
nav.rail a span{{font-size:24px;line-height:1}}nav.rail a small{{font-size:11px;margin-top:4px}}nav.rail a.on{{background:#ffffff1f;color:#fff}}
main{{margin-left:76px;padding:16px 22px;max-width:1150px}}
@media(max-width:700px){{nav.rail{{top:auto;width:100%;height:64px;flex-direction:row;justify-content:space-around;padding:0}}main{{margin:0;padding:12px 12px 80px}}}}
header{{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}}header h1{{font-size:20px;margin:4px 0}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 16px 12px;margin:14px 0}}
table{{border-collapse:collapse;width:100%}}td,th{{border-top:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:10px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;text-decoration:none;color:inherit}}.tile:hover{{border-color:var(--per)}}
.ico{{font-size:22px}}.num{{font-size:30px;font-weight:700;line-height:1.2}}
.diag{{width:100%;height:auto}}.diag text{{font-size:12px;text-anchor:middle}}.diag .w{{fill:#fff;font-weight:700}}.diag .ws{{fill:#fff;font-size:10px}}.diag .t{{fill:var(--fg)}}.diag .l{{text-anchor:start}}
details.host>summary{{cursor:pointer;list-style:none;font-size:16px;font-weight:600;padding:10px 0}}details.host>summary::-webkit-details-marker{{display:none}}
.alert{{display:flex;gap:10px;align-items:center;padding:8px 12px;border-radius:10px;background:var(--evbg);margin:6px 0}}
a{{color:inherit}}section.reports{{border-left:6px solid var(--bad)}}.rep{{padding:6px 10px;margin:8px 0;background:var(--evbg);border-radius:8px}}section.periodic{{border-left:6px solid var(--per)}}section.event{{border-left:6px solid var(--ev);background:var(--evbg)}}.evchip{{font-size:11px;border:1px solid var(--ev);color:var(--ev);border-radius:999px;padding:0 7px}}@keyframes fl{{from{{background:#f5c54266}}to{{background:transparent}}}}tr.flash td{{animation:fl 2.5s}}.chip{{display:inline-block;font-size:12px;border:1px solid var(--per);border-radius:999px;padding:0 8px;margin:2px;text-decoration:none}}.chip.ev{{border-color:var(--ev);color:var(--ev)}}.mute{{color:var(--mute);font-size:12px;font-weight:normal}}.ok{{color:var(--ok)}}.bad{{color:var(--bad);font-weight:600}}
.view{{display:none}}.view.on{{display:block}}</style>
<nav class=rail>{rail}</nav><main><header><h1>ローカル AI 運用</h1><span class=mute>更新 {time.strftime('%m/%d %H:%M')} · 10 分ごと</span></header>
{body}</main>
<script>
function show(){{var p=decodeURIComponent((location.hash||'#home').slice(1)).split('/'),v=p[0],f=document.getElementById('v-'+v);if(!f){{v='home';f=document.getElementById('v-home')}}
if(!f)return;document.querySelectorAll('.view').forEach(function(e){{e.classList.toggle('on',e===f)}});
document.querySelectorAll('nav.rail a').forEach(function(a){{a.classList.toggle('on',a.dataset.v===v)}});scrollTo(0,0);
var t=p[1]&&f.querySelector('[data-k="'+p[1].replace(/"/g,'')+'"]');if(t){{t.scrollIntoView({{block:'center'}});t.classList.remove('flash');void t.offsetWidth;t.classList.add('flash')}}}}
addEventListener('hashchange',show);show();
</script>"""


SCOPE = {"all": ("全ネットワーク", "var(--bad)"), "lan": ("家の LAN", "#d9822b"),
         "tailnet": ("Tailscale だけ", "var(--ok)"), "local": ("自分だけ", "var(--mute)")}


def match_host(target, data):
    """Which probed machine an ssh target points at (by alias in hosts.json or by address)."""
    for h in CFG["hosts"]:
        addrs = data.get(h["name"], {}).get("net", {}).get("addrs", [])
        if target in h["ssh"] or any(target.split("@")[-1] == a["addr"] for a in addrs):
            return h["name"]
    return None


def render_network(data, m):
    hosts = [h for h in data if "error" not in data[h]]
    x_hub, x_host = 30, 420
    rows, boxes, y = [], {}, 30
    for h in hosts:
        net = data[h].get("net", {})
        addrs = net.get("addrs", [])
        hgt = 34 + 16 * max(1, len(addrs))
        boxes[h] = (y, hgt)
        lines = "".join(f'<text x="{x_host + 12}" y="{y + 42 + 16 * i}" class="t l">{esc(a["iface"])}  {esc(a["addr"])}</text>'
                        for i, a in enumerate(addrs))
        rows.append(f'<rect x="{x_host}" y="{y}" width="240" height="{hgt}" rx="8" fill="none" stroke="{COLORS.get(h)}" stroke-width="2"/>'
                    f'<rect x="{x_host}" y="{y}" width="240" height="24" rx="8" fill="{COLORS.get(h)}"/>'
                    f'<text x="{x_host + 120}" y="{y + 16}" class="w">{esc(short(hostname_of(h, data), 30))}</text>{lines}')
        counts = {}
        for p in net.get("listen", []):
            counts[p["scope"]] = counts.get(p["scope"], 0) + 1
        by = y + 4
        for k in ("all", "lan", "tailnet", "local"):
            n = counts.get(k, 0)
            if n:
                name, col = SCOPE[k]
                rows.append(f'<rect x="{x_host + 270}" y="{by}" width="{min(200, n * 8)}" height="12" rx="3" fill="{col}"/>'
                            f'<text x="{x_host + 280 + min(200, n * 8)}" y="{by + 10}" class="t l" style="font-size:11px">{name} {n}</text>')
                by += 18
        y += max(hgt, by - y) + 24
    total = y
    hub_y = total / 2 - 30
    tun_by = {}
    for t in m["tunnels"]:
        tun_by.setdefault(match_host(t["target"], data), []).append(t)
    edges = []
    for h in hosts:
        hy, hg = boxes[h]
        cy = hy + hg / 2
        tl = tun_by.get(h, [])
        dash = "" if tl else 'stroke-dasharray="5,4"'
        edges.append(f'<path d="M{x_hub + 200} {hub_y + 30} C{x_hub + 290} {hub_y + 30},{x_host - 90} {cy},{x_host} {cy}" fill="none" '
                     f'stroke="{COLORS.get(h)}" stroke-width="{3 if tl else 1.5}" {dash}/>')
        labels = ["ssh"] + [f"localhost:{t['local']} → :{t['remote']}" for t in tl]
        for i, lab in enumerate(labels):
            edges.append(f'<text x="{x_host - 10}" y="{cy - 8 - 13 * (len(labels) - 1 - i)}" class="t" '
                         f'style="font-size:10px;text-anchor:end">{esc(lab)}</text>')
    hub = (f'<rect x="{x_hub}" y="{hub_y}" width="200" height="60" rx="10" fill="{COLORS[CFG["hub"]["name"]]}"/>'
           f'<text x="{x_hub + 100}" y="{hub_y + 26}" class="w">{esc(short(HUB_HOST, 26))}</text>'
           f'<text x="{x_hub + 100}" y="{hub_y + 44}" class="ws">{esc(CFG["hub"]["name"])}（司令塔）</text>')
    svg = f'<svg viewBox="0 0 960 {total}" class=diag>{"".join(edges)}{hub}{"".join(rows)}</svg>'
    tables = []
    for h in hosts:
        ls = [p for p in data[h].get("net", {}).get("listen", []) if p["scope"] != "local"]
        tr = "".join(f'<tr><td>{p["port"]}</td><td>{esc(p["process"]) or "<span class=mute>（名前は管理者権限が必要）</span>"}</td>'
                     f'<td style="color:{SCOPE[p["scope"]][1]}">{SCOPE[p["scope"]][0]}</td></tr>' for p in ls)
        tables.append(f'<section style="border-left:6px solid {COLORS.get(h)}"><h2>{esc(hostname_of(h, data))} <span class=mute>{esc(h)}</span></h2>'
                      f'<p class=mute>ほかの機械から届く待ち受けポート（自分だけのものは省略）</p>'
                      f'<table><tr><th>ポート</th><th>プロセス</th><th>届く範囲</th></tr>{tr}</table></section>')
    legend = " ".join(f'<span style="color:{c}">■</span> {n}' for n, c in SCOPE.values())
    body = (f'<section><h2>つながり方</h2><p class=mute>太線はトンネルあり、点線は ssh だけ。'
            f'右の棒は待ち受けポートの数（届く範囲ごと）。{legend}</p>{svg}</section>{"".join(tables)}')
    return body


def event_units():
    return {e.get("unit", "").removesuffix(".service") for e in CFG.get("events", [])}


def events_section(data):
    """Jobs that start when something happens outside (webhooks), kept apart from the clock-driven ones."""
    rules = CFG.get("events", [])
    if not rules:
        return ""
    seen = [dict(e, host=h) for h, d in data.items() for e in (d.get("events") or [])]
    now = time.strftime("%Y-%m-%d")
    rows = []
    for r in rules:
        hits = [e for e in seen if re.search(r.get("match", "$^"), e["type"]) and (not r.get("source") or e["source"] == r["source"])]
        today = sum(e["date"] == now for e in hits)
        last = f'{hits[-1]["date"][5:]} {hits[-1]["time"]}' if hits else "まだなし"
        rows.append(f'<tr data-k="{esc(r.get("key") or r.get("name", ""))}"><td><b>{esc(r.get("name", ""))}</b><div class=mute>{esc(r.get("source", ""))}: <code>{esc(r.get("match", ""))}</code></div></td>'
                    f'<td>{esc(r.get("unit", ""))}<div class=mute>{esc(r.get("host", ""))}</div></td><td>{today}</td><td>{esc(last)}</td></tr>')
    recent = "".join(f'<li><span class=mute>{esc(e["date"][5:])} {esc(e["time"])}</span> {esc(e["source"])} <code>{esc(e["type"])}</code> {esc(e["repo"])}</li>'
                     for e in seen[-10:][::-1]) or '<li class=mute>まだイベントはありません</li>'
    return (f'<section class=event><h2>⚡ イベントで動く仕事 <span class=mute>外で何かが起きたら動く。中身を人が確かめる仕事が多い</span></h2>'
            f'<table><tr><th>引き金</th><th>動かす仕事</th><th>今日</th><th>最後</th></tr>{"".join(rows)}</table>'
            f'<h3>最近届いたイベント</h3><ul>{recent}</ul></section>')


LEVEL = {"high": ("高", "var(--bad)"), "medium": ("中", "var(--ev)"), "low": ("低", "var(--per)"),
         "info": ("情報", "var(--mute)"), "test": ("テスト", "var(--mute)")}


def reports_section(data):
    rs = sorted((dict(r, host=h) for h, d in data.items() for r in (d.get("reports") or [])), key=lambda r: r["time"], reverse=True)
    if not rs:
        return ""
    items = []
    for r in rs[:15]:
        name, col = LEVEL.get(r["level"], ("?", "var(--mute)"))
        items.append(f'<div class=rep style="border-left:4px solid {col}"><div><b style="color:{col}">{name}</b> '
                     f'<span class=mute>{esc(r["time"][:16])} · {esc(r["source"])} · {esc(hostname_of(r["host"], data))}</span></div>'
                     f'<div><b>{esc(r["title"])}</b></div><div class=mute style="white-space:pre-wrap">{esc(r["summary"])}</div>'
                     + (f'<div class=mute>{esc(r["link"])}</div>' if r.get("link") else "") + "</div>")
    high = sum(r["level"] == "high" for r in rs[:15])
    return (f'<section class=reports><h2>🛡 エージェントの報告 <span class=mute>新しい順・色は危険度'
            f'{f" · 高 {high} 件" if high else ""}</span></h2>{"".join(items)}</section>')


def out_timers(data):
    """Every clock-driven job on one table, machine shown by its color, failures first."""
    rows = []
    for h, d in data.items():
        for t in d.get("timers", []):
            name = t["unit"].removesuffix(".service")
            ev = ' <span class=evchip>⚡</span>' if name in event_units() else ""
            rows.append((t["result"] == "success", h, f'<tr data-k="{esc(name)}"><td><span style="color:{COLORS.get(h)}">●</span> {esc(hostname_of(h, data))}</td>'
                         f'<td><b>{esc(name)}</b>{ev}<div class=mute>{esc(note(t["unit"]) or t["description"])}</div></td>'
                         f'<td>{strip(t["schedule"], COLORS.get(h))}</td>'
                         f'<td class={"ok" if t["result"] == "success" else "bad"}>{"✅" if t["result"] == "success" else "❌ " + esc(t["result"])}</td></tr>'))
        for line in d.get("cron", []):
            sched, cmd = cron_schedule(line)
            if sched is None:
                continue
            name = cron_name(cmd)
            rows.append((True, h, f'<tr data-k="{esc(name)}"><td><span style="color:{COLORS.get(h)}">●</span> {esc(hostname_of(h, data))}</td>'
                         f'<td><b>{esc(name)}</b> <span class=mute>cron</span><div class=mute>{esc(NOTES.get(name, "") or cmd[:90])}</div></td>'
                         f'<td>{strip(sched, COLORS.get(h)) if sched.startswith("OnCalendar") else esc(sched)}</td>'
                         f'<td class=mute title="cron は結果を記録しない">―</td></tr>'))
    rows.sort(key=lambda r: (r[0], r[1]))
    yield ('<section class=periodic><h2>🕒 すべての定期の仕事 <span class=mute>失敗が上・帯は24時間、赤線は今</span></h2>'
           '<table><tr><th>マシン</th><th>仕事</th><th>いつ</th><th>結果</th></tr>' + "".join(r[2] for r in rows) + '</table></section>')


def render(data, m):
    out = []
    for host, d in data.items():
        if "error" in d:
            out.append(f'<section style="border-left:6px solid {COLORS.get(host)}"><h2>{esc(host)}</h2><p class="bad">届かない: {esc(d["error"])}</p></section>')
            continue
        p2m = {s["port"]: ", ".join(s["models"]) for s in d["llm_servers"]}
        llms = "".join(f"<li><b>:{s['port']}</b> {esc(', '.join(s['models']))} <span class=mute>({esc(s['process'])})</span></li>"
                       for s in d["llm_servers"]) or "<li class=mute>なし</li>"
        rows = "".join(
            f"<tr><td><b>{esc(t['unit'].removesuffix('.service'))}</b>{' <span class=evchip>⚡ イベントでも起動</span>' if t['unit'].removesuffix('.service') in event_units() else ''}<div class=mute>{esc(note(t['unit']) or t['description'])}</div></td>"
            f"<td>{strip(t['schedule'], COLORS.get(host))}</td><td>{esc(t['last'])}</td>"
            f"<td class={'ok' if t['result'] == 'success' else 'bad'}>{esc(t['result'])}</td>"
            f"<td>{llm_cell(t['llm'], p2m)}</td></tr>" for t in d["timers"])
        svcs = "".join(
            f"<tr><td><b>{esc(s['unit'].removesuffix('.service'))}</b> <span class=mute>{s['scope']}</span>"
            f"<div class=mute>{esc(note(s['unit']) or s['description'])}</div></td><td>{llm_cell(s['llm'], p2m)}</td></tr>"
            for s in d["services"])
        cron = "".join(f"<li><code>{esc(c[:140])}</code></li>" for c in d["cron"]) or "<li class=mute>なし</li>"
        out.append(f"""<section style="border-left:6px solid {COLORS.get(host)}"><h2>{esc(d['host'])} <span class=mute>{esc(host)}</span></h2>
<p class=mute>{esc(d['gpu'])} · RAM {esc(d['mem'])} · {esc(d['uptime'])}</p>
<h3>動いている LLM</h3><ul>{llms}</ul>
<h3>🕒 定期の仕事（タイマー）</h3><table><tr><th>仕事</th><th>いつ <span class=mute>0 · 6 · 12 · 18 · 24時、赤線は今</span></th><th>最後</th><th>結果</th><th>使う LLM</th></tr>{rows or '<tr><td colspan=5 class=mute>なし</td></tr>'}</table>
<h3>常駐サービス</h3><table><tr><th>サービス</th><th>使う LLM</th></tr>{svcs}</table>
<h3>cron</h3><ul>{cron}</ul></section>""")
    uses = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in m["uses"])
    agents = "".join(f"<li><code>{esc(a[-1])}</code> {'動作中' if a[0] != '-' else '待機'}"
                     f"{'' if a[1] == '0' else f' <span class=bad>終了コード {esc(a[1])}</span>'}</li>" for a in m["agents"])
    out.insert(0, f"""<section style="border-left:6px solid {COLORS[CFG['hub']['name']]}"><h2>{esc(HUB_HOST)} <span class=mute>{esc(CFG['hub']['name'])} · {esc(CFG['hub'].get('subtitle', ''))}</span></h2><table><tr><th>何が</th><th>どの LLM を使う</th></tr>{uses}</table>
{f"<h3>LaunchAgent（トンネル・監視）</h3><ul>{agents}</ul>" if agents else ""}</section>""")
    home, jobs, evs = overview(data)
    alerts = [f'<div class=alert>❌ <b>{esc(hostname_of(h, data))}</b> {esc(t["unit"].removesuffix(".service"))} <span class=mute>{esc(t["last"])}</span></div>'
              for h, d in data.items() for t in d.get("timers", []) if t["result"] != "success"]
    alerts += [f'<div class=alert>🚨 <b>{esc(r["title"])}</b> <span class=mute>{esc(r["time"][:16])}</span></div>'
               for d in data.values() for r in (d.get("reports") or []) if r["level"] == "high"][-3:]
    attention = f'<section><h2>👀 気にしてほしいこと</h2>{"".join(alerts)}</section>' if alerts else ""
    hosts = "".join(f'<details class=host><summary>{s_[s_.index("<h2>") + 4:s_.index("</h2>")]}</summary>{s_}</details>' for s_ in out)
    views = {"home": home + attention, "jobs": jobs + "".join(out_timers(data)), "events": evs or "<section><p class=mute>イベントの規則がありません</p></section>",
             "reports": reports_section(data) or "<section><p class=mute>まだ報告はありません</p></section>",
             "machines": hosts, "net": render_network(data, m),
             "assets": assets.render_assets(data, hostname_of, COLORS), "roadmap": assets.render_roadmap(data), "prices": assets.render_prices(data),
             "org": org.render(CFG.get("agents", []), data, {k: v[1] for k, v in LEVEL.items()}, CFG.get("events", []))}
    return page("運用", "", views)


def pushed(name):
    """A snapshot a machine sent itself (push.py) to ~/.ai-ops/push/<name>.json; marked when it is old."""
    f = Path.home() / f".ai-ops/push/{name}.json"
    if not f.exists():
        return {"error": "まだ届いていない（push.py が動いていない）"}
    d = json.loads(f.read_text())
    age = (time.time() - f.stat().st_mtime) / 60
    if age > 30:
        d["stale"] = f"{age / 60:.1f} 時間前の情報（送り元がスリープ中かも）" if age > 90 else f"{age:.0f} 分前の情報"
    return d


def collect(config, yes):
    load_config(config)
    data = {h["name"]: pushed(h["push"]) if h.get("push") else probe(h["name"], h["ssh"], yes, h.get("installed", False))
            for h in CFG["hosts"]}
    (HERE / "last.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    m = hub()
    (HERE / "dashboard.html").write_text(render(data, m))
    (HERE / "network.html").write_text(page("ネットワーク", render_network(data, m)))
    return data


def main():
    ap = argparse.ArgumentParser(description="Who does what on your local AI machines, as one HTML page.")
    ap.add_argument("--config", type=Path, default=CONF_DIR / "hosts.json")
    ap.add_argument("--yes", action="store_true", help="accept the read-only probe for every host without asking")
    a = ap.parse_args()
    collect(a.config, a.yes)
    print("wrote", HERE / "dashboard.html")


if __name__ == "__main__":
    main()
