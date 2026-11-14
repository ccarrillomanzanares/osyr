#!/bin/sh
# Turns this OSYR machine into an unattended test bench: it opens a reverse
# SSH tunnel to a server, so that the developer can log in from there and run
# tests. Nothing listens on the network of this machine: the tunnel goes out,
# and the SSH server it carries only listens on 127.0.0.1.
#
# Not part of the image. Everything lives in the home folder (it survives
# image updates) and runs as the user's own services; no root is needed.
#
#   sh bench-setup.sh USER@SERVER [SSH_PORT]   set up and start
#   sh bench-setup.sh --remove                 stop and delete everything
#
# What it does:
#   1. Creates a key for the tunnel and sends its public half to the server
#      inside an osyr-diag (the description of the problem).
#   2. Waits until the server accepts that key. The server answers with the
#      public key that will be allowed to log in here.
#   3. Starts two user services: an SSH server on 127.0.0.1:2022 that only
#      accepts that key, and the tunnel (server's 127.0.0.1:2222 -> here).
set -eu

DIR="$HOME/.config/osyr-bench"
UNITS="$HOME/.config/systemd/user"
LOCAL_PORT=2022
REMOTE_PORT=2222

if [ "${1:-}" = "--remove" ]; then
    systemctl --user disable --now osyr-bench-tunnel.service osyr-bench-sshd.service 2>/dev/null || true
    rm -f "$UNITS/osyr-bench-tunnel.service" "$UNITS/osyr-bench-sshd.service"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf "$DIR"
    echo "Test bench removed."
    exit 0
fi

[ $# -ge 1 ] || { echo "usage: bench-setup.sh USER@SERVER [SSH_PORT] | --remove" >&2; exit 2; }
SERVER=$1
SSH_PORT=${2:-22}
[ "$(id -u)" != 0 ] || { echo "Run it as the normal user, not as root." >&2; exit 2; }

mkdir -p "$DIR" "$UNITS"
chmod 700 "$DIR"
[ -f "$DIR/tunnel_key" ] || ssh-keygen -q -t ed25519 -N "" -C "osyr-bench-$(hostname)" -f "$DIR/tunnel_key"
[ -f "$DIR/host_key" ] || ssh-keygen -q -t ed25519 -N "" -C "osyr-bench-host" -f "$DIR/host_key"

cat > "$DIR/sshd_config" <<EOF
Port $LOCAL_PORT
ListenAddress 127.0.0.1
HostKey $DIR/host_key
PidFile none
AuthorizedKeysFile $DIR/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowTcpForwarding no
X11Forwarding no
Subsystem sftp internal-sftp
EOF

ssh_server() {
    ssh -i "$DIR/tunnel_key" -p "$SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" \
        -o ConnectTimeout=10 "$@"
}

if ! [ -s "$DIR/authorized_keys" ]; then
    echo "Sending this machine's public key to the server (osyr-diag)..."
    if ! osyr-diag --quick --no-screenshots --message "osyr-bench key: $(cat "$DIR/tunnel_key.pub")"; then
        echo "osyr-diag failed. Give this line to the developer by hand:"
        cat "$DIR/tunnel_key.pub"
    fi
    echo "Waiting for the server to accept the key (up to 60 min; this can be left alone)..."
    n=0
    while :; do
        key=$(ssh_server "$SERVER" 2>/dev/null || true)
        case "$key" in
            ssh-ed25519\ *) printf '%s\n' "$key" > "$DIR/authorized_keys"; chmod 600 "$DIR/authorized_keys"; break ;;
        esac
        n=$((n + 1))
        [ "$n" -lt 360 ] || { echo "The server did not accept the key. Run this again later." >&2; exit 1; }
        sleep 10
    done
    echo "Accepted."
fi

cat > "$UNITS/osyr-bench-sshd.service" <<EOF
[Unit]
Description=OSYR test bench: SSH server on 127.0.0.1:$LOCAL_PORT

[Service]
ExecStart=/usr/sbin/sshd -D -e -f $DIR/sshd_config
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat > "$UNITS/osyr-bench-tunnel.service" <<EOF
[Unit]
Description=OSYR test bench: reverse tunnel to $SERVER
After=osyr-bench-sshd.service

[Service]
ExecStart=/usr/bin/ssh -N -T -i $DIR/tunnel_key -p $SSH_PORT -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$DIR/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=20 -R 127.0.0.1:$REMOTE_PORT:127.0.0.1:$LOCAL_PORT $SERVER
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable osyr-bench-sshd.service osyr-bench-tunnel.service
systemctl --user restart osyr-bench-sshd.service osyr-bench-tunnel.service
sleep 5
if systemctl --user is-active -q osyr-bench-sshd.service && systemctl --user is-active -q osyr-bench-tunnel.service; then
    echo "Test bench ready. Leave this machine on, plugged in and with the lid open."
else
    echo "A service did not start:" >&2
    status=$(systemctl --user --no-pager status osyr-bench-sshd.service osyr-bench-tunnel.service 2>&1 || true)
    printf '%s\n' "$status" >&2
    osyr-diag --quick --no-screenshots --message "osyr-bench failed:
$status" >&2 || true
    exit 1
fi
