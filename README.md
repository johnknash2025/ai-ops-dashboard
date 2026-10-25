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

## Use

    mkdir -p ~/.config/ai-ops-dashboard
    cp hosts.example.json ~/.config/ai-ops-dashboard/hosts.json   # list your machines and ssh targets
    python3 collect.py                                            # answer y per machine, then open dashboard.html

`--yes` accepts for every host without asking (for scripts, after you have read the list above).
`hosts.json` also takes short notes per job (`"notes": {"unit-name": "what it is for"}`) and what the hub itself uses.
Your config, consent record, `last.json` and `dashboard.html` stay out of git.

## License

MIT
