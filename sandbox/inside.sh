#!/bin/sh
# Runs inside the container (see sandbox/session): sets up the home folder of
# a machine that has already been through its first start, and starts the chat.
set -eu
cp -r /run/osyr/skel/. "$HOME"/
mkdir -p "$HOME/.config/osyr" "$HOME/.local/state/osyr" "$HOME/.local/share/osyr"
: > "$HOME/.local/state/osyr/firstboot-done"
if [ -S /run/osyr/model.sock ]; then
    # The model is outside: a relay gives the chat a local address for it
    python3 /run/osyr/relay.py 127.0.0.1 8090 /run/osyr/model.sock &
    (umask 077; cat > "$HOME/.config/osyr/ai.json" <<JSON
{"active": "sandbox", "providers": {"sandbox": {"url": "http://127.0.0.1:8090/v1", "model": "",
 "context": ${OSYR_SANDBOX_CONTEXT:-16384}}}}
JSON
    )
fi
[ $# -gt 0 ] && exec "$@"
exec osyr-chat
