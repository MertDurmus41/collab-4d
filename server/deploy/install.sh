#!/usr/bin/env bash
# Installs the Collab 4D session server on a Linux machine (Ubuntu, Debian, Oracle Linux, ...) as a service that
# starts with the machine and restarts if it stops. Normally run for you by deploy.sh; by hand, from the unpacked
# bundle folder:
#
#     sudo bash install.sh [--port 47800] [--password TEXT] [--foreground]
#
# Running it again updates the server and keeps the password (stored in /etc/collab4d.env).
# --foreground: no service, run the server in this terminal (for tests and machines without systemd).
set -euo pipefail

PORT=47800
PASSWORD=""
FOREGROUND=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --foreground) FOREGROUND=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/collab4d
ENV_FILE=/etc/collab4d.env
UNIT=/etc/systemd/system/collab4d.service

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi
case "$PORT" in
    ''|*[!0-9]*) echo "port must be a number" >&2; exit 2 ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
    echo "port must be between 1024 and 65535" >&2
    exit 2
fi

say() { printf '\n== %s\n' "$*"; }

# 1. Python 3 -------------------------------------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    say "Installing Python 3"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q python3
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q python3
    else
        echo "Python 3 is missing and no known package manager was found" >&2
        exit 1
    fi
fi
PYTHON="$(command -v python3)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' || {
    echo "Python 3.6 or newer is needed ($("$PYTHON" -V 2>&1) found)" >&2
    exit 1
}

# 2. Files ------------------------------------------------------------------------------------------------------------
say "Copying the server to $DEST"
[ -f "$HERE/run_server.py" ] && [ -d "$HERE/lib/c4d_collab_lib/core" ] || {
    echo "bundle incomplete: run_server.py and lib/c4d_collab_lib/core must sit next to install.sh" >&2
    exit 1
}
rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp -R "$HERE/lib" "$HERE/run_server.py" "$DEST.new/"
find "$DEST.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
chmod -R a+rX "$DEST"
"$PYTHON" -c "import sys; sys.path.insert(0, '$DEST/lib'); import c4d_collab_lib.core.server" || {
    echo "the server does not import on this Python" >&2
    exit 1
}

# 3. Password and settings ----------------------------------------------------------------------------------------------
if [ -z "$PASSWORD" ] && [ -f "$ENV_FILE" ]; then
    PASSWORD="$(sed -n 's/^C4D_COLLAB_PASSWORD=//p' "$ENV_FILE" | head -n 1)"
fi
if [ -z "$PASSWORD" ]; then
    PASSWORD="$("$PYTHON" -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(16)))')"
fi
case "$PASSWORD" in
    *[[:space:]]*|*\"*|*\'*|*\\*|*\$*) echo "the password may not contain spaces, quotes, backslashes or \$" >&2; exit 2 ;;
esac
if [ "${#PASSWORD}" -lt 4 ]; then
    echo "the password needs at least 4 characters" >&2
    exit 2
fi
umask 077
cat > "$ENV_FILE" <<EOF
C4D_COLLAB_PASSWORD=$PASSWORD
C4D_COLLAB_PORT=$PORT
C4D_COLLAB_SESSION="Collab 4D"
PYTHONUNBUFFERED=1
EOF
umask 022

# 4. Firewall on the machine itself (the cloud provider's own firewall is a separate setting) ---------------------------
say "Opening TCP port $PORT"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null
    echo "ufw: allowed $PORT/tcp"
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --quiet --permanent --add-port="$PORT/tcp"
    firewall-cmd --quiet --reload
    echo "firewalld: allowed $PORT/tcp"
elif command -v iptables >/dev/null 2>&1 && iptables -S INPUT >/dev/null 2>&1; then
    # Oracle's Ubuntu images end the INPUT chain with a REJECT rule: our rule must come before it.
    if ! iptables -C INPUT -p tcp --dport "$PORT" -m state --state NEW -j ACCEPT 2>/dev/null; then
        iptables -I INPUT 1 -p tcp --dport "$PORT" -m state --state NEW -j ACCEPT
    fi
    if command -v netfilter-persistent >/dev/null 2>&1; then
        netfilter-persistent save >/dev/null 2>&1 || true
    elif [ -d /etc/iptables ]; then
        iptables-save > /etc/iptables/rules.v4
    fi
    echo "iptables: allowed $PORT/tcp"
else
    echo "no firewall found on the machine (nothing to open)"
fi

# 5. Run ----------------------------------------------------------------------------------------------------------------
show_result() {
    cat <<EOF

==================================================================
 Collab 4D server is running.
 Port:     $PORT
 Password: $PASSWORD
 In Cinema 4D: Host address = this machine's public IP, Port and Password as above, then Join.
 The first person to join shares the scene they have open.
==================================================================
EOF
}

if [ "$FOREGROUND" -eq 1 ] || ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
    id -u collab4d >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin collab4d 2>/dev/null || true
    show_result
    say "Running in the foreground (no systemd). Ctrl+C stops it."
    set -a; . "$ENV_FILE"; set +a
    if id -u collab4d >/dev/null 2>&1 && command -v runuser >/dev/null 2>&1; then
        exec runuser -u collab4d -- env C4D_COLLAB_PASSWORD="$C4D_COLLAB_PASSWORD" C4D_COLLAB_PORT="$C4D_COLLAB_PORT" \
            C4D_COLLAB_SESSION="$C4D_COLLAB_SESSION" PYTHONUNBUFFERED=1 "$PYTHON" "$DEST/run_server.py"
    fi
    exec "$PYTHON" "$DEST/run_server.py"
fi

say "Installing the service"
cat > "$UNIT" <<EOF
[Unit]
Description=Collab 4D session server
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON $DEST/run_server.py
DynamicUser=yes
Restart=always
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=700M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable collab4d >/dev/null 2>&1
systemctl restart collab4d
sleep 2
if ! systemctl is-active --quiet collab4d; then
    journalctl -u collab4d -n 30 --no-pager >&2 || true
    echo "the service did not start" >&2
    exit 1
fi
show_result
echo " Logs: sudo journalctl -u collab4d -f"
