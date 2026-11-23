#!/bin/sh
# Installs or refreshes the web sandbox on this machine (ccmai.org/osyr/try/).
# Run through 'scripts/publish.sh sandbox'. Needs sudo. Safe to repeat.
#
#   /opt/osyr-sandbox/root           the image root (build/output/target) without the models,
#                                    with this repository's osyr code over it; root's, read-only
#   /opt/osyr-sandbox/sandbox        session, server.py, modelgate.py...
#   /opt/osyr-sandbox/tests/doubles  the pretend hardware
#   /etc/osyr-sandbox/config.json    the service's settings (created once, then left alone)
#   /etc/osyr-sandbox/key            the model server's key, if the settings name it
#   /etc/osyr-sandbox/off            the switch: while this file exists nobody gets in
#   /var/log/osyr-sandbox/           one line per session
#   WEB/try/                         the page
#
# Two users, neither can log in: 'osyr-try' runs the service and holds the
# key; 'osyr-sandbox' runs the sessions (through sudo) and can read neither
# the key nor anybody's home folder.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
DEST=/opt/osyr-sandbox
ETC=/etc/osyr-sandbox
WEB=${OSYR_WEB:-/var/www/ccmai.org/osyr}
SRC_ROOT=$REPO/build/output/target
OVERLAY=$REPO/board/osyr/rootfs-overlay

[ -x "$SRC_ROOT/usr/bin/python3" ] || { echo "install: no image root in $SRC_ROOT (run 'make')" >&2; exit 1; }
command -v bwrap >/dev/null || { echo "install: bwrap is not installed" >&2; exit 1; }

for u in osyr-try osyr-sandbox; do
    id "$u" >/dev/null 2>&1 || sudo useradd --system --user-group --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin "$u"
done
# The sessions' limits are scopes of their user's own systemd
sudo loginctl enable-linger osyr-sandbox

sudo install -d -m 0755 "$DEST" "$DEST/tests" "$ETC"
sudo rsync -a --delete --chown=root:root --exclude '/usr/share/osyr/models/*' "$SRC_ROOT/" "$DEST/root/"
sudo rsync -a --delete --chown=root:root "$OVERLAY/usr/lib/osyr/" "$DEST/root/usr/lib/osyr/"
sudo install -m 0755 "$OVERLAY/usr/bin/osyr-chat" "$DEST/root/usr/bin/osyr-chat"
sudo install -m 0644 "$OVERLAY/usr/share/osyr/help.md" "$DEST/root/usr/share/osyr/help.md"
sudo find "$DEST/root" -name __pycache__ -prune -exec rm -rf {} +
# Nothing of the build machine goes in: the upload token is this machine's
sudo rm -f "$DEST/root/etc/osyr/diag.token"
sudo touch "$DEST/root/etc/osyr/diag.token"
sudo rsync -a --delete --chown=root:root --exclude __pycache__ --exclude install.sh "$HERE/" "$DEST/sandbox/"
sudo rsync -a --delete --chown=root:root --exclude __pycache__ "$REPO/tests/doubles/" "$DEST/tests/doubles/"
sudo chmod -R a+rX,go-w "$DEST/sandbox" "$DEST/tests"
git -C "$REPO" rev-parse --short HEAD | sudo tee "$DEST/commit" >/dev/null

if ! sudo test -f "$ETC/config.json"; then
    code=$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')
    sudo tee "$ETC/config.json" >/dev/null <<EOF
{
 "port": 8095,
 "upstream": "http://127.0.0.1:18190/v1",
 "extra": {"chat_template_kwargs": {"enable_thinking": false}},
 "warm_minutes": 20,
 "state": "/var/lib/osyr-sandbox/prefix.json",
 "context": 16384,
 "sessions": 2,
 "code": "$code",
 "off_file": "$ETC/off",
 "log": "/var/log/osyr-sandbox/sessions.log",
 "work": "/run/osyr-sandbox",
 "session": "$DEST/sandbox/session",
 "session_prefix": ["sudo", "-n", "-u", "osyr-sandbox"]
}
EOF
    echo "install: new settings in $ETC/config.json (access code: $code)"
fi
sudo chown osyr-try:root "$ETC/config.json"
sudo chmod 0600 "$ETC/config.json"
if sudo test -f "$ETC/key"; then
    sudo chown osyr-try:root "$ETC/key"
    sudo chmod 0600 "$ETC/key"
fi

tmp=$(mktemp)
echo 'osyr-try ALL=(osyr-sandbox) NOPASSWD: /opt/osyr-sandbox/sandbox/session' > "$tmp"
sudo visudo -c -f "$tmp" >/dev/null
sudo install -m 0440 "$tmp" /etc/sudoers.d/osyr-sandbox
rm -f "$tmp"

sudo tee /etc/systemd/system/osyr-sandbox.service >/dev/null <<EOF
[Unit]
Description=OSYR web sandbox (ccmai.org/osyr/try)
After=network.target

[Service]
User=osyr-try
Group=osyr-sandbox
ExecStart=/usr/bin/python3 $DEST/sandbox/server.py $ETC/config.json
Restart=on-failure
RestartSec=5
RuntimeDirectory=osyr-sandbox
RuntimeDirectoryMode=0750
LogsDirectory=osyr-sandbox
StateDirectory=osyr-sandbox
LogsDirectoryMode=0750
# The sessions start through sudo, so no NoNewPrivileges here; the rest is closed
ProtectSystem=strict
# (not ProtectHome: it hides /run/user, where the sessions reach their systemd)
InaccessiblePaths=/home /root
PrivateTmp=yes
PrivateDevices=no
# (no ProtectKernelTunables or ProtectControlGroups: with /proc partly covered, the
# sessions' containers cannot mount their own)
ProtectKernelModules=yes
# (inherited by the sessions: bwrap sets up its empty network over netlink)
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
LimitNOFILE=4096

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable osyr-sandbox.service >/dev/null 2>&1
sudo systemctl restart osyr-sandbox.service

sudo install -d -m 0755 "$WEB/try" "$WEB/try/vendor"
sudo install -m 0644 "$REPO/web/try/index.html" "$WEB/try/index.html"
sudo install -m 0644 "$REPO"/web/try/vendor/* "$WEB/try/vendor/"

sleep 1
systemctl is-active --quiet osyr-sandbox.service || { echo "install: the service did not start:" >&2
    sudo journalctl -u osyr-sandbox.service -n 20 --no-pager >&2; exit 1; }
curl -fsS "http://127.0.0.1:$(sudo python3 -c "import json; print(json.load(open('$ETC/config.json')).get('port', 8095))")/status"
echo
echo "SANDBOX INSTALLED in $DEST ($(cat "$DEST/commit")). The web server needs, once, in the site's settings:"
echo "    ProxyPass /osyr/try/ws     http://127.0.0.1:8095/ws upgrade=websocket"
echo "    ProxyPass /osyr/try/status http://127.0.0.1:8095/status"
