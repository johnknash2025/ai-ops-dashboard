# ai-ops-dashboard

[English](README.md) | 日本語

「どのマシンでどのモデルが動いていて、どのジョブがそれに依存しているのか」に答えるための、1枚の HTML ページです。
ホームラボのローカル LLM マシン向けで、自分の ssh で各マシンに届いていくつか読みとり、
ハブとマシン構成の図、定期ジョブの直近実行時刻を並べた 24 時間分のグラフ、マシンごとのテーブルを描きます。

- Python 3 の標準ライブラリだけ。インストール不要。マシン側は systemd を使う Linux、ハブは macOS でも Linux でも動きます。
- 読みとり専用。あなたのマシンは何も変更せず、どこへも送信しません。ページはローカルファイルです。
- 必ず確認してから読みとります。各マシンで初めて読む前に、何を読むのかを正確に列挙して `y` を待ちます。
  答えは記憶され、プローブスクリプトか ssh ターゲットが変わるたびに再び聞かれます。

## 何を読むのか（各マシン上で、`remote_probe.py` 経由）

- 127.0.0.1 で応答している LLM サーバー（OpenAI 互換の `/v1/models`、Ollama の `/api/ps`）
- systemd のユーザータイマー（スケジュール、直近の実行、結果）、実行中のユーザーサービス、`/etc/systemd/system` 下のサービス
- crontab の行
- それらのユニットが起動するスクリプト、加えてその作業ディレクトリにある `config.json` / `config.yaml` / `settings.json`。
  ポート番号とモデル名を取り出すためだけです（「このジョブはどの LLM を使っているか」という推測）。`.env` ファイルは決して読みません。
- GPU / RAM の使用状況と稼働時間（uptime）
- `~/.ai-ops/reports/` にある最新の agent レポートと `~/agent-inbox/events/` の webhook イベント（そのフォルダが存在する場合）
- ネットワークアドレスと、プロセス名つきの listen 中 TCP ポート。どのポートに誰が届けるかを表します

ハブ側（macOS）では、ファイル名が `launchd_match` に一致する LaunchAgents も読んで、`ssh -L` トンネルを描きます。
ページは 2 枚出ます: `dashboard.html`（ジョブとモデル）と `network.html`（アドレス、トンネル、ポートの公開状況）。

## 使い方

    mkdir -p ~/.config/ai-ops-dashboard
    cp hosts.example.json ~/.config/ai-ops-dashboard/hosts.json   # マシンと ssh ターゲットを列挙
    python3 collect.py                                            # マシンごとに y に答えてから dashboard.html を開く

`--yes` は、上の一覧を読んだうえで（スクリプトから使う場合）すべての host に確認なしで承認します。
`hosts.json` にはジョブごとの短いメモ（`"notes": {"unit-name": "what it is for"}`）や、ハブ自身が使っているものも書けます。
設定、同意の記録、`last.json`、`dashboard.html` は git に入りません。

## 組織（Organisation）

`hosts.json` の `"agents"` に agent を並べると、🏢 ビューは 4 層で描きます — owners（責任範囲ごとに 1 人）、
builders（頼まれた変更をつくり、別の誰かがレビュー）、guardians（読みとり専用の見張り）、base parts（キュー、インボックス、モデルのエンドポイント）。
各 agent のジョブは、プローブが拾った結果に応じて緑 / 赤で示されます。

    {"name": "reviewer", "layer": "owner|builder|guardian|base", "scope": "what it answers for",
     "can": ["read", "draft", "pr", "comment"], "model": "which LLM", "local_only": true, "confidential": false,
     "jobs": ["unit or cron script names"], "reviews": "the builder it checks"}

構造のヘルスチェックも走らせます: 誰も owner ではないジョブ、自分自身や同じモデルにチェックされている builder、
書き込み権限を持つ guardian、local-only でないのに機密データを読む agent、agent ごとの失敗しているジョブ。

## Agent レポート

任意の agent は、そのマシン上の `~/.ai-ops/reports/` に小さな JSON ファイルを書くだけで、dashboard に調査結果を出せます:

    {"time": "2026-10-26T06:49:00", "source": "security agent", "level": "high|medium|low|info|test",
     "title": "one line", "summary": "a few lines", "link": "where the full report is"}

プローブが読むのは最新の 30 ファイルだけで、フィールドも上記のものだけ。ページは新しい順に、レベル色で並べます。

## 常時運転（たとえば DGX Spark クラスのボックスやホームサーバー）

    python3 collect.py                      # そのマシンのターミナルで 1 回: host ごとに y に答える
    python3 serve.py                        # 127.0.0.1:8795、10 分ごとに更新
    tailscale serve --bg --https=8795 http://127.0.0.1:8795   # HTTPS は tailnet 内だけ

`hosts.json` で `"ssh": []` は、運転しているマシン自身を指します。他のマシンについては、運転するマシンに
プローブだけを実行できる鍵を持たせます: `./install-probe-key.sh <its .pub> <its addresses> <ssh target>` は
ターゲットの `authorized_keys` に `restrict` と `from=` をつけた forced command として追加し、その host エントリーに `"installed": true` を入れます。

## ライセンス

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)。2026-10-31 以前に公開されたバージョンは MIT でも利用可能です。
