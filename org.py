"""Organisation view: draw the agents from the "agents" list in hosts.json in four layers, and check the structure.

Each agent: {"name", "layer": "owner|builder|guardian|base", "scope": "what it is responsible for",
             "can": ["read", "draft", "pr", "comment", "write", ...], "model": "which LLM (or none)",
             "local_only": true if it must stay on local models, "confidential": true if it reads mail/health/money,
             "jobs": ["systemd unit or cron script names it owns"], "reviews": "name of the agent whose work it checks",
             "starts": ["other ways it starts, e.g. 'label agent:go'"], "flow": ["step", ...], "output": "where results go"}
Event rules (top-level "events") that name the agent in "agent", or whose "unit" is one of its jobs, are linked as triggers.
Checks: jobs nobody owns, a builder reviewed by itself or by the same model, guardians that can write,
confidential agents that are not local-only, owners without any job, agents whose jobs are failing.
"""
import html

LAYERS = [("owner", "担当（範囲ごとの責任者）", "c-teal"), ("builder", "作る係（担当から依頼される）", "c-coral"),
          ("guardian", "横断の番人（読むだけ）", "c-purple"), ("base", "基盤（判断しない部品）", "c-gray")]
WRITE = {"write", "pr", "merge", "delete", "deploy", "config"}


def esc(s):
    return html.escape(str(s))


UNIT_SUFFIXES = (".service", ".timer", ".path", ".socket", ".target", ".mount", ".slice")


def unit_stem(name):
    """Unit name without its type suffix, same rule as collect.py (kept local to avoid a circular import)."""
    for suf in UNIT_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def job_status(data):
    """unit/script name -> (ok?, host). cron jobs have no result, so they count as ok."""
    st = {}
    for h, d in data.items():
        for t in d.get("timers", []):
            st[unit_stem(t["unit"])] = (t["result"] == "success", h)
        for s in d.get("services", []):
            st.setdefault(unit_stem(s["unit"]), (True, h))
        for line in d.get("cron", []):
            import re
            m = re.findall(r"([\w.-]+)\.(?:py|sh|js)", line)
            if m:
                st.setdefault(m[-1], (True, h))
    return st


def checks(agents, data):
    st = job_status(data)
    by = {a["name"]: a for a in agents}
    owned = {j for a in agents for j in a.get("jobs", [])}
    out = []
    timers = {unit_stem(t["unit"]) for d in data.values() for t in d.get("timers", [])}
    orphans = sorted(timers - owned)
    if orphans:
        out.append(("medium", f"持ち主のいない定期の仕事 {len(orphans)} 件", "、".join(orphans[:8]) + (" ほか" if len(orphans) > 8 else "")))
    for a in agents:
        if a.get("layer") == "guardian" and WRITE & set(a.get("can", [])):
            out.append(("high", f"番人「{a['name']}」に書き込み権限", "番人は読むだけにする: " + ", ".join(sorted(WRITE & set(a["can"])))))
        if a.get("confidential") and not a.get("local_only"):
            out.append(("high", f"機密を読む「{a['name']}」がローカル限定でない", "メール・健康・お金を読む係はローカルのモデルだけにする"))
        if a.get("layer") == "builder" and "pr" in a.get("can", []):
            rev = [r for r in agents if r.get("reviews") == a["name"]]
            if not rev:
                out.append(("medium", f"「{a['name']}」の変更を確かめる係がいない", "別のエージェント（できれば別のモデル）にレビューさせる"))
            for r in rev:
                if r["name"] == a["name"] or (r.get("model") and r.get("model") == a.get("model")):
                    out.append(("medium", f"「{a['name']}」と確かめる係が同じモデル", f"{r['name']} も {r.get('model')}。見落としが同じになりやすい"))
        if a.get("layer") == "owner" and not a.get("jobs"):
            out.append(("info", f"担当「{a['name']}」に仕事がまだない", "名簿だけで、動いている仕事が結びついていない"))
        bad = [j for j in a.get("jobs", []) if j in st and not st[j][0]]
        if bad:
            out.append(("medium", f"「{a['name']}」の仕事が失敗中", "、".join(bad)))
        missing = [j for j in a.get("jobs", []) if j not in st]
        if missing:
            out.append(("info", f"「{a['name']}」の仕事が見つからない", "、".join(missing) + "（名前違いか、止まっている）"))
    return out


def workflows(agents, data, events):
    """One row per agent: what starts it (links into the 🕒 / ⚡ views), its steps and where the result goes."""
    st = job_status(data)
    rows = []
    for a in agents:
        trig = []
        for j in a.get("jobs", []):
            ok = st.get(j, (None, None))[0]
            mark = "" if ok is None else ("" if ok else " ❌")
            trig.append(f'<a href="#jobs/{esc(j)}" class=chip>🕒 {esc(j)}{mark}</a>' if j in st else f'<span class="chip mute">{esc(j)}（未検出）</span>')
        for e in events:
            if e.get("agent") == a["name"] or e.get("unit") in a.get("jobs", []):
                trig.append(f'<a href="#events/{esc(e.get("key") or e["name"])}" class="chip ev">⚡ {esc(e["name"])}</a>')
        trig += [f'<span class="chip ev">✋ {esc(x)}</span>' for x in a.get("starts", [])]
        flow = " → ".join(esc(x) for x in a.get("flow", [])) or '<span class=mute>（手順はまだ書かれていない）</span>'
        out = a.get("output")
        ova = f'<div class=mute>→ {esc(out)}</div>' if out else ""
        rows.append(f'<tr><td><b>{esc(a["name"])}</b><div class=mute>{esc(a.get("model", ""))}</div></td>'
                    f'<td>{" ".join(trig) or "<span class=mute>なし</span>"}</td><td>{flow}'
                    f'{ova}</td>'
                    f'<td class=mute>{esc(", ".join(a.get("can", [])))}</td></tr>')
    return ('<section><h2>🔁 エージェントとワークフロー <span class=mute>🕒 定期 · ⚡ イベント · ✋ 人の合図。押すとその仕事へ</span></h2>'
            '<table><tr><th>エージェント</th><th>何で動く</th><th>手順と出し先</th><th>権限</th></tr>' + "".join(rows) + '</table></section>')


def render(agents, data, level_colors, events=()):
    if not agents:
        return ('<section><h2>🏢 組織</h2><p class=mute>hosts.json に "agents" の名簿がありません。'
                'README の「組織」の節を見て、エージェントごとに層・担当範囲・権限・仕事を書くと、ここに組織図と診断が出ます。</p></section>')
    st = job_status(data)
    rows, y = [], 20
    for key, label, ramp in LAYERS:
        members = [a for a in agents if a.get("layer") == key]
        if not members:
            continue
        n = len(members)
        per = min(4, n)
        lines = (n + per - 1) // per
        hgt = 40 + lines * 66
        rows.append(f'<rect x="10" y="{y}" width="900" height="{hgt}" rx="12" fill="none" stroke="var(--line)" stroke-dasharray="4 4"/>'
                    f'<text x="26" y="{y + 22}" class="t l" style="font-weight:600">{esc(label)}</text>')
        w = (860 - (per - 1) * 14) / per
        for i, a in enumerate(members):
            cx = 30 + (i % per) * (w + 14)
            cy = y + 36 + (i // per) * 66
            jobs = a.get("jobs", [])
            bad = sum(1 for j in jobs if j in st and not st[j][0])
            ok = sum(1 for j in jobs if j in st and st[j][0])
            dot = "var(--bad)" if bad else ("var(--ok)" if ok else "var(--mute)")
            fill = {"c-teal": "#1d9e75", "c-coral": "#d85a30", "c-purple": "#7f77dd", "c-gray": "#888780"}[ramp]
            sub = a.get("model") or ""
            rows.append(f'<g><title>{esc(a.get("scope", ""))}\n権限: {esc(", ".join(a.get("can", [])))}\n仕事: {esc(", ".join(jobs))}</title>'
                        f'<rect x="{cx:.0f}" y="{cy}" width="{w:.0f}" height="54" rx="8" fill="{fill}" opacity=".14" stroke="{fill}"/>'
                        f'<circle cx="{cx + 14:.0f}" cy="{cy + 16}" r="5" fill="{dot}"/>'
                        f'<text x="{cx + 26:.0f}" y="{cy + 21}" class="t l" style="font-weight:600">{esc(a["name"])}</text>'
                        f'<text x="{cx + 14:.0f}" y="{cy + 41}" class="t l" style="font-size:11px;fill:var(--mute)">'
                        f'{esc(sub)}{" · 🔒" if a.get("confidential") else ""}{f" · 仕事 {len(jobs)}" if jobs else ""}</text></g>')
        y += hgt + 14
    svg = f'<svg viewBox="0 0 920 {y}" class=diag>{"".join(rows)}</svg>'
    found = checks(agents, data)
    items = "".join(f'<div class=rep style="border-left:4px solid {level_colors.get(lv, "var(--mute)")}"><b>{esc(t)}</b>'
                    f'<div class=mute>{esc(d)}</div></div>' for lv, t, d in sorted(found, key=lambda x: ["high", "medium", "info"].index(x[0])))
    return (f'<section><h2>🏢 組織 <span class=mute>点: 緑=仕事は成功 赤=失敗中 灰=仕事なし · 枠にカーソルで担当範囲と権限</span></h2>{svg}</section>'
            + workflows(agents, data, events) +
            f'<section class=reports><h2>🩺 組織の健康診断 <span class=mute>{len(found)} 件</span></h2>'
            f'{items or "<p class=mute>問題は見つかりませんでした</p>"}</section>')
