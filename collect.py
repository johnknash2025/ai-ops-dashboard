#!/usr/bin/env python3
"""Collect who-does-what from every machine and write dashboard.html (local file, summary only, no secrets)."""
import argparse
import hashlib
import html
import os
import re
import sys
import json
import subprocess
import time
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
It changes nothing, sends nothing anywhere, and the result stays in {out} on this computer.
The consent is remembered in {consent} and asked again whenever the probe script changes."""
CFG, COLORS, NOTES = {}, {}, {}


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


def probe(name, ssh, yes):
    how = "ssh " + " ".join(ssh)
    if not consented(name, how, yes):
        return {"error": "not allowed yet: run collect.py in a terminal and answer y"}
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", *ssh, "python3 -"], input=PROBE,
                           capture_output=True, text=True, timeout=180)
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
    return {"agents": agents, "uses": h.get("uses", [])}


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
    tiles = "".join(f'<div class=tile><div class=num style="color:{c}">{n}</div>{esc(l)}</div>' for n, l, c in
                    [(llms, "LLM サーバー", "var(--fg)"), (ok, "定期の仕事 成功", "var(--ok)"),
                     (bad, "定期の仕事 失敗", "var(--bad)" if bad else "var(--ok)"), (svcs, "常駐サービス", "var(--fg)")])
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
                    f'<text x="405" y="{my+18}" class="w">{esc(h)}</text><text x="405" y="{my+33}" class="ws">定期 {n} 件</text>')
        for s in models:
            label = esc(s["models"][0][:28]) + (f" ほか{len(s['models'])-1}" if len(s["models"]) > 1 else "")
            rows.append(f'<path d="M490 {my+20} C540 {my+20},530 {y+18},570 {y+18}" stroke="{c}" fill="none" stroke-opacity=".5"/>'
                        f'<rect x="570" y="{y}" width="330" height="36" rx="6" fill="none" stroke="{c}"/>'
                        f'<text x="582" y="{y+23}" class="t l">:{s["port"]} {label}</text>')
            y += 46
        y += 8 if len(models) == 1 else 0
    hub = (f'<rect x="20" y="{hub_y}" width="170" height="40" rx="8" fill="{COLORS[CFG["hub"]["name"]]}"/>'
           f'<text x="105" y="{hub_y+18}" class="w">{esc(CFG["hub"]["name"])}</text><text x="105" y="{hub_y+33}" class="ws">{esc(CFG["hub"].get("subtitle", ""))}</text>')
    diagram = f'<svg viewBox="0 0 920 {y+10}" class=diag>{hub}{"".join(rows)}</svg>'
    # 24h chart
    lane = 34
    dots, labels = [], []
    for i, h in enumerate(hosts):
        cy = 30 + i * lane
        labels.append(f'<text x="80" y="{cy+4}" class="t" text-anchor="end">{esc(h)}</text>'
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
    return (f'<div class=tiles>{tiles}</div><section><h2>どれが何を受け持つか</h2>{diagram}</section>'
            f'<section><h2>定期の仕事が最後に動いた時刻 <span class=mute>赤は失敗・点にカーソルで名前</span></h2>{chart}</section>')


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
            f"<tr><td><b>{esc(t['unit'].removesuffix('.service'))}</b><div class=mute>{esc(note(t['unit']) or t['description'])}</div></td>"
            f"<td>{esc(t['schedule'])}</td><td>{esc(t['last'])}</td>"
            f"<td class={'ok' if t['result'] == 'success' else 'bad'}>{esc(t['result'])}</td>"
            f"<td>{llm_cell(t['llm'], p2m)}</td></tr>" for t in d["timers"])
        svcs = "".join(
            f"<tr><td><b>{esc(s['unit'].removesuffix('.service'))}</b> <span class=mute>{s['scope']}</span>"
            f"<div class=mute>{esc(note(s['unit']) or s['description'])}</div></td><td>{llm_cell(s['llm'], p2m)}</td></tr>"
            for s in d["services"])
        cron = "".join(f"<li><code>{esc(c[:140])}</code></li>" for c in d["cron"]) or "<li class=mute>なし</li>"
        out.append(f"""<section style="border-left:6px solid {COLORS.get(host)}"><h2>{esc(host)} <span class=mute>{esc(d['host'])}</span></h2>
<p class=mute>{esc(d['gpu'])} · RAM {esc(d['mem'])} · {esc(d['uptime'])}</p>
<h3>動いている LLM</h3><ul>{llms}</ul>
<h3>定期の仕事（タイマー）</h3><table><tr><th>仕事</th><th>いつ</th><th>最後</th><th>結果</th><th>使う LLM</th></tr>{rows or '<tr><td colspan=5 class=mute>なし</td></tr>'}</table>
<h3>常駐サービス</h3><table><tr><th>サービス</th><th>使う LLM</th></tr>{svcs}</table>
<h3>cron</h3><ul>{cron}</ul></section>""")
    uses = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in m["uses"])
    agents = "".join(f"<li><code>{esc(a[-1])}</code> {'動作中' if a[0] != '-' else '待機'}"
                     f"{'' if a[1] == '0' else f' <span class=bad>終了コード {esc(a[1])}</span>'}</li>" for a in m["agents"])
    out.insert(0, f"""<section style="border-left:6px solid {COLORS[CFG['hub']['name']]}"><h2>{esc(CFG['hub']['name'])} <span class=mute>{esc(CFG['hub'].get('subtitle', ''))}</span></h2><table><tr><th>何が</th><th>どの LLM を使う</th></tr>{uses}</table>
<h3>LaunchAgent（トンネル・監視）</h3><ul>{agents}</ul></section>""")
    return f"""<!doctype html><meta charset=utf-8><title>AI 運用ダッシュボード</title>
<style>:root{{--bg:#fff;--fg:#1b1b1b;--mute:#777;--line:#e4e4e4;--ok:#17803d;--bad:#c0392b}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#e8e8e8;--mute:#999;--line:#30323a;--ok:#4cc27a;--bad:#ff7a6b}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"Hiragino Sans",sans-serif;max-width:1100px;margin:auto;padding:16px}}
section{{border:1px solid var(--line);border-radius:10px;padding:4px 16px 12px;margin:14px 0}}
table{{border-collapse:collapse;width:100%}}td,th{{border-top:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}.tile{{border:1px solid var(--line);border-radius:10px;padding:10px 14px}}.num{{font-size:28px;font-weight:700}}
.diag{{width:100%;height:auto}}.diag text{{font-size:12px;text-anchor:middle}}.diag .w{{fill:#fff;font-weight:700}}.diag .ws{{fill:#fff;font-size:10px}}.diag .t{{fill:var(--fg)}}.diag .l{{text-anchor:start}}
.mute{{color:var(--mute);font-size:12px;font-weight:normal}}.ok{{color:var(--ok)}}.bad{{color:var(--bad);font-weight:600}}</style>
<h1>ローカル AI 運用ダッシュボード</h1><p class=mute>更新 {time.strftime('%F %H:%M')} · 「使う LLM」はスクリプトから推定</p>
{overview(data)}{''.join(out)}"""


def main():
    ap = argparse.ArgumentParser(description="Who does what on your local AI machines, as one HTML page.")
    ap.add_argument("--config", type=Path, default=CONF_DIR / "hosts.json")
    ap.add_argument("--yes", action="store_true", help="accept the read-only probe for every host without asking")
    a = ap.parse_args()
    load_config(a.config)
    data = {h["name"]: probe(h["name"], h["ssh"], a.yes) for h in CFG["hosts"]}
    (HERE / "last.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (HERE / "dashboard.html").write_text(render(data, hub()))
    print("wrote", HERE / "dashboard.html")


if __name__ == "__main__":
    main()
