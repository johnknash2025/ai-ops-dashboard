"""Assets view (hardware, OS, storage, tool versions, git checkouts per machine) and roadmap view (from agent data)."""
import html


def esc(s):
    return html.escape(str(s))


def bar(used, size):
    pct = used / size * 100 if size else 0
    col = "var(--bad)" if pct >= 90 else ("var(--ev)" if pct >= 80 else "var(--ok)")
    return (f'<div style="background:var(--line);border-radius:4px;height:8px;width:160px;display:inline-block;vertical-align:middle">'
            f'<div style="width:{min(100, pct):.0f}%;background:{col};height:8px;border-radius:4px"></div></div> '
            f'<span class=mute>{used:,} / {size:,} GB（{pct:.0f}%）</span>')


def render_assets(data, hostname_of, colors):
    hosts = [h for h in data if "inventory" in data[h]]
    if not hosts:
        return "<section><p class=mute>まだ資産の情報がありません（各機械の probe を新しくすると出ます）</p></section>"
    inv = {h: data[h]["inventory"] for h in hosts}
    head = "".join(f'<th><span style="color:{colors.get(h)}">●</span> {esc(hostname_of(h, data))}</th>' for h in hosts)
    rows = [("OS", lambda i: i["os"]), ("カーネル", lambda i: i["kernel"]), ("CPU", lambda i: f'{i["cpu"]}（{i["cores"]} スレッド）'),
            ("メモリ", lambda i: f'{i["ram_gb"]} GB'), ("GPU", None)]
    body = ""
    for label, f in rows:
        cells = "".join(f"<td>{esc(f(inv[h]) if f else data[h].get('gpu', ''))}</td>" for h in hosts)
        body += f"<tr><th>{label}</th>{cells}</tr>"
    tools = sorted({t for i in inv.values() for t in i["tools"]})
    for t in tools:
        vals = [inv[h]["tools"].get(t, "") for h in hosts]
        body += f"<tr><th class=mute>{esc(t)}</th>" + "".join(f"<td class=mute>{esc(v)}</td>" for v in vals) + "</tr>"
    storage = ""
    for h in hosts:
        items = "".join(f'<div>{bar(d["used_gb"], d["size_gb"])} <code>{esc(d["mount"])}</code> <span class=mute>{esc(d["fs"])}</span></div>'
                        for d in sorted(inv[h]["disks"], key=lambda d: -d["size_gb"]))
        pools = "".join(f'<div class=mute>ZFS {esc(p["name"])}: {esc(p["alloc"])} / {esc(p["size"])} · {esc(p["health"])}</div>' for p in inv[h]["zpools"])
        storage += f'<h3><span style="color:{colors.get(h)}">●</span> {esc(hostname_of(h, data))}</h3>{items}{pools}'
    repos = ""
    for h in hosts:
        r = "".join(f'<tr><td><code>{esc(x["dir"])}</code></td><td class=mute>{esc(x["remote"].replace("https://github.com/", ""))}</td>'
                    f'<td>{esc(x["tag"])}</td><td class=mute>{esc(x["last_commit"])}</td></tr>' for x in inv[h]["repos"])
        if r:
            repos += (f'<details><summary><span style="color:{colors.get(h)}">●</span> {esc(hostname_of(h, data))}（{len(inv[h]["repos"])}）</summary>'
                      f'<table><tr><th>フォルダ</th><th>元</th><th>版</th><th>最終コミット</th></tr>{r}</table></details>')
    nas = ""
    for h in hosts:
        sec = inv[h].get("security")
        if not sec:
            continue
        good = {"firewall": "on", "filevault": "on", "upnp_router": "FALSE", "myqnapcloud_publish": "0", "telnet": "FALSE",
                "ftp": "False", "snmp": "FALSE", "login_failures_recent": "0", "malware_events_recent": "0", "admin_disabled": "1"}
        cells = "".join(f'<span class=chip style="border:1px solid {"var(--ok)" if good.get(k) == v else ("var(--bad)" if k in good else "var(--line)")};'
                        f'border-radius:999px;padding:1px 9px;margin:2px;display:inline-block;font-size:12px">{esc(k)} = {esc(v)}</span>'
                        for k, v in sec.items())
        extra = ""
        if inv[h].get("raid"):
            extra += "<div class=mute>RAID: " + esc(" · ".join(r for r in inv[h]["raid"] if not r.startswith(("md9 ", "md13 ")))) + "</div>"
        if inv[h].get("containers"):
            extra += "<div class=mute>コンテナ: " + esc(" · ".join(f'{c["name"]} ({c["image"]})' for c in inv[h]["containers"])) + "</div>"
        if inv[h].get("apps"):
            extra += f'<details><summary class=mute>アプリ {len(inv[h]["apps"])} 個</summary><div class=mute>' + esc(
                " · ".join(f'{a["name"]} {a["version"]}{"" if a["enabled"] == "TRUE" else "（停止）"}' for a in inv[h]["apps"])) + "</div></details>"
        nas += f'<h3><span style="color:{colors.get(h)}">●</span> {esc(hostname_of(h, data))}</h3><div>{cells}</div>{extra}'
    minio = ""
    for h in data:
        mi = (data[h].get("data") or {}).get("minio")
        if not mi:
            continue
        total = sum(b["size_gb"] for b in mi["buckets"]) or 1
        rows = "".join(f'<tr><td><b>{esc(b["name"])}</b>{" 🔐" if "secret" in b["name"] else ""}</td>'
                       f'<td><div style="background:var(--line);border-radius:4px;height:8px;width:180px;display:inline-block">'
                       f'<div style="width:{max(1, b["size_gb"] / total * 100):.0f}%;background:var(--per);height:8px;border-radius:4px"></div></div> '
                       f'<span class=mute>{b["size_gb"]:,} GB</span></td><td class=mute>{esc(", ".join(b["prefixes"]))}</td></tr>'
                       for b in sorted(mi["buckets"], key=lambda b: -b["size_gb"]))
        minio += (f'<section><h2>🪣 データ（MinIO） <span class=mute>{esc(hostname_of(h, data))} · {esc(mi["image"])} · '
                  f'バケット {len(mi["buckets"])} 個 · 合計 {total:,.0f} GB</span></h2><table><tr><th>バケット</th><th>大きさ</th><th>中の最上位</th></tr>{rows}</table></section>')
    stale = "".join(f'<div class=alert>⏳ {esc(hostname_of(h, data))}: {esc(data[h]["stale"])}</div>' for h in data if data[h].get("stale"))
    return (stale + (f'<section><h2>🛡 セキュリティの設定 <span class=mute>緑=望ましい 赤=見直し</span></h2>{nas}</section>' if nas else "") + minio +
            f'<section><h2>🖥 機械とソフトの版</h2><div style="overflow-x:auto"><table><tr><th></th>{head}</tr>{body}</table></div></section>'
            f'<section><h2>💾 ストレージ <span class=mute>80% で黄、90% で赤</span></h2>{storage}</section>'
            f'<section><h2>📦 手元のリポジトリ <span class=mute>ホーム直下の git のクローン</span></h2>{repos}</section>')


def render_roadmap(data):
    items = [(h, d["data"]["roadmap"]) for h, d in data.items() if (d.get("data") or {}).get("roadmap")]
    if not items:
        return "<section><p class=mute>ロードマップのデータがまだありません（毎週の roadmap が ~/.ai-ops/data/roadmap.json に書きます）</p></section>"
    out = []
    for _, rm in items:
        for t in rm.get("tools", []):
            sure = "".join(f'<li>✅ <a href="{esc(x["url"])}" target=_blank rel=noopener>{esc(x["title"])}</a></li>' for x in t.get("sure", [])[:4])
            likely = "".join(f'<li>🔜 {"🆕 " if x.get("new") else ""}<a href="{esc(x["url"])}" target=_blank rel=noopener>{esc(x["title"])}</a></li>'
                             for x in t.get("likely", [])[:5])
            out.append(f'<section><h2>{esc(t["name"])} <span class=mute>最新 {esc(t.get("latest") or "?")} · {esc(t.get("next") or "")}'
                       f'{f" · 今週の新しい項目 {t["new"]}" if t.get("new") else ""}</span></h2><ul>{sure}{likely}</ul>'
                       f'<p class=mute>{esc(t.get("mine") or "")}</p></section>')
        out.insert(0, f'<p class=mute>更新 {esc(rm.get("updated", ""))} · ✅ ほぼ確定 🔜 可能性が高い · 全文は Obsidian の「ロードマップ」</p>')
    return "".join(out)
