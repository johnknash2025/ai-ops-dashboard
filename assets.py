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
    link = lambda x, t: f'<a href="{esc(x["url"])}" target=_blank rel=noopener>{esc(t)}</a>'
    out, tl = [], []
    for _, rm in items:
        for t in rm.get("tools", []):
            for r in t.get("releases", [])[:3]:
                tl.append((r["date"], t["name"], "済", link(r, r["tag"]) + (f' <span class=mute>{esc(r["note"][:90])}</span>' if r.get("note") else "")))
            if t.get("next_date"):
                tl.append((t["next_date"], t["name"], "目安", f'次の版（マージ済み {len(t.get("merged", []))} 件が入る見込み）'))
            for m in t.get("milestones", []):
                if m.get("due"):
                    tl.append((m["due"], t["name"], "予定", link(m, m["title"]) + f' <span class=mute>残り {m["open"]}</span>'))
            merged = "".join(f'<li>📦 {link(x, x["title"])} <span class=mute>{esc(x.get("date", ""))}</span></li>' for x in t.get("merged", [])[:8])
            sure = "".join(f'<li>✅ {link(x, x["title"])}</li>' for x in t.get("sure", [])[:4])
            likely = "".join(f'<li>🔜 {"🆕 " if x.get("new") else ""}{link(x, x["title"])}</li>' for x in t.get("likely", [])[:5])
            watch = "".join(f'<li>👀 {link(x, x["title"])} <span class=mute>👍{x.get("votes", 0)}</span></li>' for x in t.get("watch", [])[:3])
            rels = " · ".join(f'{link(r, r["tag"])} <span class=mute>{esc(r["date"])}</span>' for r in t.get("releases", [])[:5])
            out.append(f'<section><h2>{esc(t["name"])} <span class=mute>最新 {esc(t.get("latest") or "?")} · {esc(t.get("next") or "")}'
                       f'{f" · 今週の新しい項目 {t["new"]}" if t.get("new") else ""}</span></h2>'
                       f'{f"<p class=mute>最近の版: {rels}</p>" if rels else ""}'
                       f'{f"<h3>次の版に入る（マージ済み）</h3><ul>{merged}</ul>" if merged else ""}'
                       f'{f"<h3>入りそう</h3><ul>{sure}{likely}</ul>" if sure or likely else ""}'
                       f'{f"<h3>要望（様子見）</h3><ul>{watch}</ul>" if watch else ""}'
                       f'<p class=mute>{esc(t.get("mine") or "")}</p></section>')
        upd = rm.get("updated", "")
    tl.sort(key=lambda x: x[0], reverse=True)
    rows = "".join(f'<tr><td>{esc(d)}</td><td>{esc(n)}</td><td>{k}</td><td>{h}</td></tr>' for d, n, k, h in tl[:40])
    head = (f'<p class=mute>更新 {esc(upd)} · 📦 マージ済み＝次の版にほぼ確実に入る · ✅ マイルストーン付き · 🔜 保守者の進行中 PR · '
            f'「目安」は過去の間隔からの推定で公式の予定ではない · 全文は Obsidian の「ロードマップ」</p>'
            f'<section><h2>🗓 時系列</h2><table><tr><th>日付</th><th>ツール</th><th></th><th>内容</th></tr>{rows}</table></section>')
    return head + "".join(out)


def render_prices(data):
    items = [d["data"]["prices"] for d in data.values() if (d.get("data") or {}).get("prices")]
    if not items:
        return "<section><p class=mute>価格のデータがまだありません（調達係の pricewatch が 1 日 2 回 ~/.ai-ops/data/prices.json に書きます）</p></section>"
    pr = items[0]
    yen = lambda v: f"{v:,} 円" if isinstance(v, int) else "—"
    rows = []
    for q, d in pr.get("items", {}).items():
        for cond in ("新品", "中古"):
            v = d.get(cond)
            if not v:
                continue
            mark = " 🔻" if v.get("low30") and v["price"] < v["low30"] else ""
            rows.append(f'<tr><td>{esc(q)}</td><td>{cond}</td><td><b>{yen(v["price"])}</b>{mark}</td><td>{yen(v.get("low30"))}</td>'
                        f'<td><a href="{esc(v["url"])}" target=_blank rel=noopener>{esc(v["shop"])}</a></td>'
                        f'<td class=mute>{esc(v["name"][:60])}</td><td>{v.get("count", "")}</td></tr>')
    led = "".join(f'<tr><td>{esc(o["item"])}</td><td>{esc(o.get("where", ""))}</td><td>{yen(o.get("market_used"))}</td>'
                  f'<td>{yen(o.get("market_new"))}</td><td class=mute>{esc(o.get("note", ""))}</td></tr>' for o in pr.get("ledger", []))
    bl = ""
    for b in pr.get("builds", []):
        parts = "".join(f'<li>{esc(x["part"])} ×{x["n"]}: {yen(x["price"])}（<a href="{esc(x["url"])}" target=_blank rel=noopener>{esc(x["shop"])}</a>）</li>'
                        for x in b["parts"])
        miss = f'<li class=mute>在庫なし: {esc("・".join(b["missing"]))}</li>' if b["missing"] else ""
        tr = [t for _, t in b.get("trend", [])]
        trend = (f' · 最初 {yen(tr[0])} → 今 {yen(tr[-1])}（{(tr[-1] - tr[0]) * 100 // tr[0]:+d}%、最安 {yen(min(tr))}、{len(tr)} 日分）'
                 if len(tr) > 1 else "")
        bl += (f'<section><h3>{esc(b["name"])} — {yen(b["total"]) if b["total"] else "部品がそろわず"}'
               f' <span class=mute>· 電気代 約 {b["power_month"]:,} 円/月{trend}</span></h3><ul>{parts}{miss}</ul></section>')
    ws = pr.get("whole_server") or {}
    if ws:
        bl += '<section><h3>比較: 完成品の EPYC サーバー</h3><ul>' + "".join(
            f'<li>{c}: {yen(v["price"])}（<a href="{esc(v["url"])}" target=_blank rel=noopener>{esc(v["shop"])}</a>）'
            f'<span class=mute> {esc(v["name"][:70])}</span></li>' for c, v in ws.items()) + '</ul></section>'
    if bl:
        bl = ('<section><h2>🏗 最先端 MoE 用の自作構成（1ソケット 12ch / 2ソケット 24ch）</h2><p class=mute>今日の最安から。CPU・マザー・メモリ・クーラー・電源'
              '（ケース・GPU は別）。電気代は平均 450W / 700W・31 円/kWh の目安。</p></section>' + bl)
    return (f'<p class=mute>更新 {esc(pr.get("updated", ""))} · 在庫を持つ店だけ（{esc("・".join(pr.get("shops", [])))}）· 在庫ありの最安 · '
            f'🔻＝30日の最安を更新 · 買う・売るは持ち主が判断（エージェントは案まで）</p>'
            f'<section><h2>💴 今日の最安</h2><table><tr><th>品目</th><th>状態</th><th>最安</th><th>30日の最安</th><th>店</th><th>商品</th><th>件数</th></tr>'
            f'{"".join(rows)}</table></section>'
            f'<section><h2>📒 持っている機材</h2><table><tr><th>機材</th><th>場所</th><th>中古相場</th><th>新品</th><th>メモ</th></tr>{led}</table>'
            f'<p class=mute>中古相場は店頭の売値の最安。売るときの買取価格はこれより低い。</p></section>' + bl)
