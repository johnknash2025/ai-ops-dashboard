"""install-probe-key.sh is the one step that writes on another machine: it copies remote_probe.py and adds one
forced-command line to authorized_keys. The effect of that step is tested here, whatever transport the script
uses to reach the target: ssh, scp and rsync are all stubbed to write into the same fake target."""
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TRANSPORT_STUB = """#!/bin/bash
joined="$*"
case "$joined" in
  *remote_probe.py*)
    mkdir -p "$FAKE_HOME/.ai-ops"
    src=""
    for a in "$@"; do [ -f "$a" ] && src="$a"; done
    if [ -n "$src" ]; then cat "$src" > "$FAKE_HOME/.ai-ops/remote_probe.py"
    else cat > "$FAKE_HOME/.ai-ops/remote_probe.py"; fi;;
  *authorized_keys*)
    line=$(cat); b64=$(printf '%s' "$line" | awk '{print $2}')
    mkdir -p "$FAKE_HOME/.ssh"; touch "$FAKE_HOME/.ssh/authorized_keys"
    if grep -qF "$b64" "$FAKE_HOME/.ssh/authorized_keys"; then echo already there;
    else printf '%s\\n' "$line" >> "$FAKE_HOME/.ssh/authorized_keys"
         chmod 600 "$FAKE_HOME/.ssh/authorized_keys"; echo added; fi;;
  *) echo "stub $0 got: $joined" >&2; exit 2;;
esac
"""
ADDRS = "192.0.2.10,100.64.0.10"


def section(text, needle):
    parts = [p for p in text.split("## ") if needle in p]
    assert parts, f"no section about {needle}"
    return parts[0]


@pytest.fixture
def fake_target(tmp_path):
    bin_dir, home = tmp_path / "bin", tmp_path / "target"
    bin_dir.mkdir()
    for name in ("ssh", "scp", "rsync"):
        stub = bin_dir / name
        stub.write_text(TRANSPORT_STUB)
        stub.chmod(0o755)
    pub = tmp_path / "server-probe.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPubkeymaterialforthetest probe@hub\n")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_HOME": str(home)}
    return tmp_path, home, pub, env


def run(args, tmp_path, env):
    return subprocess.run(["bash", str(HERE / "install-probe-key.sh"), *args], cwd=tmp_path,
                          capture_output=True, text=True, env=env, timeout=60)


def test_it_writes_the_probe_script_and_one_forced_command_line(fake_target):
    tmp_path, home, pub, env = fake_target
    r = run([str(pub), ADDRS, "probe@target.invalid"], tmp_path, env)
    assert r.returncode == 0, r.stderr
    assert (home / ".ai-ops/remote_probe.py").read_text() == (HERE / "remote_probe.py").read_text()
    lines = (home / ".ssh/authorized_keys").read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith('command="/usr/bin/python3 .ai-ops/remote_probe.py",restrict,'
                               f'from="{ADDRS}" ssh-ed25519 ')
    assert (home / ".ssh/authorized_keys").stat().st_mode & 0o777 == 0o600


def test_running_it_twice_does_not_add_the_key_twice(fake_target):
    tmp_path, home, pub, env = fake_target
    run([str(pub), ADDRS, "probe@target.invalid"], tmp_path, env)
    second = run([str(pub), ADDRS, "probe@target.invalid"], tmp_path, env)
    assert "already there" in second.stdout
    assert len((home / ".ssh/authorized_keys").read_text().splitlines()) == 1


def test_a_key_that_is_not_ed25519_is_refused(fake_target):
    tmp_path, home, pub, env = fake_target
    pub.write_text("ssh-rsa AAAAB3NzaC1yc2E dummy rsa@hub\n")
    r = run([str(pub), ADDRS, "probe@target.invalid"], tmp_path, env)
    assert r.returncode != 0 and "ed25519" in r.stdout + r.stderr
    assert not (home / ".ssh/authorized_keys").exists()
