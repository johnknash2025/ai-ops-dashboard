#!/bin/bash
# Let an always-on dashboard machine read another machine's status, and nothing more.
# Copies remote_probe.py to TARGET:~/.ai-ops/ and adds the dashboard machine's key to TARGET's authorized_keys as
#   command="/usr/bin/python3 .ai-ops/remote_probe.py",restrict,from="<addresses>" <key>
# so that key can only run the read-only probe (no shell, no forwarding, no pty), and only from those addresses.
# Usage: ./install-probe-key.sh <pubkey-file> <from-addresses> <ssh target and options...>
#   ./install-probe-key.sh server-probe.pub 192.0.2.10,100.64.0.10 user@workstation.local
set -euo pipefail
cd "$(dirname "$0")"
pub=$1; from=$2; shift 2
key=$(cat "$pub")
case "$key" in ssh-ed25519\ *) ;; *) echo "not an ed25519 public key: $pub"; exit 1;; esac
line="command=\"/usr/bin/python3 .ai-ops/remote_probe.py\",restrict,from=\"$from\" $key"
ssh "$@" 'mkdir -p ~/.ai-ops ~/.ssh && cat > ~/.ai-ops/remote_probe.py' < remote_probe.py
b64=$(echo "$key" | awk '{print $2}')
printf '%s\n' "$line" | ssh "$@" "k=\$(cat); grep -qF '$b64' ~/.ssh/authorized_keys 2>/dev/null && echo 'already there' || { printf '%s\n' \"\$k\" >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; echo added; }"
