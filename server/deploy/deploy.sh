#!/usr/bin/env bash
# Puts the Collab 4D server on a cloud machine in one go, from your own computer (Git Bash on Windows, or a
# terminal on macOS / Linux):
#
#     bash server/deploy/deploy.sh USER@ADDRESS PATH/TO/PRIVATE-KEY [--port 47800] [--password TEXT]
#
#     e.g.  bash server/deploy/deploy.sh ubuntu@203.0.113.10 ~/Downloads/ssh-key-2026-11-12.key
#
# It packs the server (the plugin's core folder and run_server.py, no Cinema 4D needed), copies it over SSH and runs
# install.sh there with sudo. At the end it prints the address, port and password to type in Cinema 4D.
# Run it again later to update the server; the password stays the same.
# --bundle-only DIR: only write the bundle into DIR (for tests), no SSH.
set -euo pipefail

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

TARGET=""
KEY=""
BUNDLE_ONLY=""
PORT=47800
INSTALL_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
        --password) INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
        --bundle-only) BUNDLE_ONLY="$2"; shift 2 ;;
        -h|--help) usage ;;
        -*) echo "unknown option: $1" >&2; usage ;;
        *) if [ -z "$TARGET" ]; then TARGET="$1"; elif [ -z "$KEY" ]; then KEY="$1"; else usage; fi; shift ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Bundle ----------------------------------------------------------------------------------------------------------
BUNDLE="$WORK/collab4d"
mkdir -p "$BUNDLE/lib/c4d_collab_lib/core"
cp "$ROOT/plugin/c4d_collab/c4d_collab_lib/__init__.py" "$BUNDLE/lib/c4d_collab_lib/"
cp "$ROOT"/plugin/c4d_collab/c4d_collab_lib/core/*.py "$BUNDLE/lib/c4d_collab_lib/core/"
cp "$ROOT/server/run_server.py" "$ROOT/server/deploy/install.sh" "$BUNDLE/"
(cd "$WORK" && tar -czf collab4d.tar.gz collab4d)

if [ -n "$BUNDLE_ONLY" ]; then
    mkdir -p "$BUNDLE_ONLY"
    cp "$WORK/collab4d.tar.gz" "$BUNDLE_ONLY/"
    echo "bundle written to $BUNDLE_ONLY/collab4d.tar.gz"
    exit 0
fi
[ -n "$TARGET" ] && [ -n "$KEY" ] || usage
[ -f "$KEY" ] || { echo "key file not found: $KEY" >&2; exit 1; }

# 2. SSH key: ssh refuses a key other users may read, and a downloaded file on Windows usually is. A private copy
#    in the temporary folder avoids that without touching the original.
cp "$KEY" "$WORK/key"
chmod 600 "$WORK/key" 2>/dev/null || true
SSH_OPTS=(-i "$WORK/key" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ServerAliveInterval=15)
if ! ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" true 2>"$WORK/ssh.err"; then
    if grep -qi "unprotected\|bad permissions\|too open" "$WORK/ssh.err" && command -v icacls >/dev/null 2>&1; then
        # Windows OpenSSH: limit the copy to the current user with an ACL instead of chmod.
        icacls "$(cygpath -w "$WORK/key" 2>/dev/null || echo "$WORK/key")" /inheritance:r /grant:r "$USERNAME:R" >/dev/null
        ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" true
    else
        cat "$WORK/ssh.err" >&2
        echo "cannot log in to $TARGET with that key (is the address right, and is port 22 open?)" >&2
        exit 1
    fi
fi

# 3. Copy and install ------------------------------------------------------------------------------------------------
echo "Copying the server to $TARGET ..."
scp -q "${SSH_OPTS[@]}" "$WORK/collab4d.tar.gz" "$TARGET:/tmp/collab4d.tar.gz"
QUOTED=""
for arg in ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}; do
    QUOTED="$QUOTED '$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")'"
done
ssh "${SSH_OPTS[@]}" "$TARGET" "set -e; rm -rf /tmp/collab4d; tar -xzf /tmp/collab4d.tar.gz -C /tmp; \
    sudo bash /tmp/collab4d/install.sh$QUOTED; rm -rf /tmp/collab4d /tmp/collab4d.tar.gz"

HOST="${TARGET#*@}"
echo
echo " Host address for Cinema 4D: $HOST"
echo " If Join cannot connect: open TCP port $PORT in the cloud provider's firewall (see server/deploy/README.md)."
