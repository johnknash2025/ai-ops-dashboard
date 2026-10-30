# ai-ops-dashboard

One HTML page that answers "which of my machines runs which model, and which jobs depend on it?"
for a home lab of local LLM boxes. It reaches each machine over your own ssh, reads a few things,
and draws a hub-and-machines diagram, a 24-hour chart of when scheduled jobs last ran, and per-machine tables.

Clock-driven jobs (systemd timers, cron) and event-driven jobs (webhooks you list under `"events"`) are never mixed on
one table: the clock side is blue (🕒, `#2f7de1`) with a 24-hour band, the event side is orange (⚡, `#b26a00`) with
chips naming the trigger. A unit that does both appears only on the event side, with its timer noted in small text.

- Python 3 standard library only, no install (needs Python 3.12+; some distros still call 3.10 `python3`, so type `python3.12`).
  Linux machines with systemd; the hub can be macOS or Linux.
- Read-only. It changes nothing on your machines and sends nothing anywhere; the page is a local file.
- Asks first. Before the first read on each machine it lists exactly what it will read and waits for `y`.
  The answer is remembered and asked again whenever the probe script or the ssh target changes.

## What it reads (on each machine, through `remote_probe.py`)

- LLM servers answering on 127.0.0.1 (OpenAI-compatible `/v1/models`, Ollama `/api/ps`)
- systemd user timers (schedule, last run, result), running user services, services under `/etc/systemd/system`
- crontab lines
- the scripts those units start, plus `config.json` / `config.yaml` / `settings.json` in their working directory,
  only to pick out port numbers and model names ("which LLM does this job use", a guess). `.env` files are never read.
- GPU / RAM usage and uptime
- the newest agent reports in `~/.ai-ops/reports/` and webhook events in `~/agent-inbox/events/`, if those folders exist
- network addresses and listening TCP ports with the process name, to show who can reach each port

On the hub (macOS) it also reads your LaunchAgents whose file name matches `launchd_match`, to draw `ssh -L` tunnels.
Two pages come out: `dashboard.html` (jobs and models) and `network.html` (addresses, tunnels, port exposure).

## Use

    python3 --version       # must be 3.12+; if not, use python3.12 for every command below
    mkdir -p ~/.config/ai-ops-dashboard
    cp hosts.example.json ~/.config/ai-ops-dashboard/hosts.json   # list your machines and ssh targets
    python3.12 collect.py                                       # answer y per machine, then open dashboard.html

collect.py and serve.py stop with a plain message like "Python 3.12 or newer is required" instead of a confusing
syntax error when started on an older interpreter.

`--yes` accepts for every host without asking (for scripts, after you have read the list above).
`hosts.json` also takes short notes per job (`"notes": {"unit-name": "what it is for"}`) and what the hub itself uses.
Your config, consent record, `last.json` and `dashboard.html` stay out of git.

## Organisation

List your agents under `"agents"` in `hosts.json` and the 🏢 view draws them in four layers — owners (one per area of
responsibility), builders (make changes on request, checked by someone else), guardians (read-only watchers) and base
parts (queues, inboxes, model endpoints) — with each agent's jobs marked green / red from what the probe found.

    {"name": "reviewer", "layer": "owner|builder|guardian|base", "scope": "what it answers for",
     "can": ["read", "draft", "pr", "comment"], "model": "which LLM", "local_only": true, "confidential": false,
     "jobs": ["unit or cron script names"], "reviews": "the builder it checks"}

It also runs a health check of the structure: jobs nobody owns, a builder checked by itself or by the same model,
guardians that can write, agents reading confidential data that are not local-only, failing jobs per agent.

## Agent reports

Any agent can show its findings on the dashboard by writing a small JSON file to `~/.ai-ops/reports/` on its machine:

    {"time": "2026-10-26T06:49:00", "source": "security agent", "level": "high|medium|low|info|test",
     "title": "one line", "summary": "a few lines", "link": "where the full report is"}

The probe reads only the newest 30 files and only these fields; the page lists them newest first, colored by level.

## Always-on (e.g. a DGX Spark class box or a home server)

    python3.12 collect.py                   # once, in a terminal on that machine: answer y per host
    python3.12 serve.py                     # 127.0.0.1:8795, refreshed every 10 minutes
    tailscale serve --bg --https=8795 http://127.0.0.1:8795   # HTTPS inside your tailnet only

In `hosts.json`, `"ssh": []` means the serving machine itself. For other machines, give the serving machine a key that
can only run the probe: `./install-probe-key.sh <its .pub> <its addresses> <ssh target>` adds it to the target's
`authorized_keys` as a forced command with `restrict` and `from=`, and the host entry gets `"installed": true`.

## License

MIT
