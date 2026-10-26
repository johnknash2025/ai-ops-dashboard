# ai-ops-dashboard

One HTML page that answers "which of my machines runs which model, and which jobs depend on it?"
for a home lab of local LLM boxes. It reaches each machine over your own ssh, reads a few things,
and draws a hub-and-machines diagram, a 24-hour chart of when scheduled jobs last ran, and per-machine tables.

- Python 3 standard library only, no install. Linux machines with systemd; the hub can be macOS or Linux.
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
- network addresses and listening TCP ports with the process name, to show who can reach each port

On the hub (macOS) it also reads your LaunchAgents whose file name matches `launchd_match`, to draw `ssh -L` tunnels.
Two pages come out: `dashboard.html` (jobs and models) and `network.html` (addresses, tunnels, port exposure).

## Use

    mkdir -p ~/.config/ai-ops-dashboard
    cp hosts.example.json ~/.config/ai-ops-dashboard/hosts.json   # list your machines and ssh targets
    python3 collect.py                                            # answer y per machine, then open dashboard.html

`--yes` accepts for every host without asking (for scripts, after you have read the list above).
`hosts.json` also takes short notes per job (`"notes": {"unit-name": "what it is for"}`) and what the hub itself uses.
Your config, consent record, `last.json` and `dashboard.html` stay out of git.

## Always-on (e.g. a DGX Spark class box or a home server)

    python3 collect.py                      # once, in a terminal on that machine: answer y per host
    python3 serve.py                        # 127.0.0.1:8795, refreshed every 10 minutes
    tailscale serve --bg --https=8795 http://127.0.0.1:8795   # HTTPS inside your tailnet only

In `hosts.json`, `"ssh": []` means the serving machine itself. For other machines, give the serving machine a key that
can only run the probe: `./install-probe-key.sh <its .pub> <its addresses> <ssh target>` adds it to the target's
`authorized_keys` as a forced command with `restrict` and `from=`, and the host entry gets `"installed": true`.

## License

MIT
