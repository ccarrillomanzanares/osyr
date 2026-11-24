#!/bin/sh
# Runs inside the container (see sandbox/session): sets up the home folder of
# a machine that has already been through its first start, and starts the chat.
set -eu
python3 /run/osyr/doubles/hw.py --sysfs /sys
# Buildroot's note in the build tree's root is not on a laptop: a model that lists / saw it
# (only where the root can be written, --system; elsewhere it stays)
[ "${OSYR_SYSTEM_LOCKED:-0}" != 1 ] || rm -f /THIS_IS_NOT_YOUR_ROOT_FILESYSTEM 2>/dev/null || true
# (the benchmark starts from an empty home folder and puts its own files in)
[ "${OSYR_SANDBOX_SKEL:-1}" = 0 ] || cp -r /run/osyr/skel/. "$HOME"/
mkdir -p "$HOME/.config/osyr" "$HOME/.local/state/osyr" "$HOME/.local/share/osyr"
: > "$HOME/.local/state/osyr/firstboot-done"
if [ -S /run/osyr/model.sock ]; then
    # The model is outside: a relay gives the chat a local address for it
    python3 /run/osyr/relay.py 127.0.0.1 8090 /run/osyr/model.sock &
    # With the model server itself there too (the benchmark), the chat uses that one and
    # this is its second model, the one it hands hard tasks to
    active=sandbox
    [ -S /run/osyr/llama.sock ] && active=local
    (umask 077; cat > "$HOME/.config/osyr/ai.json" <<JSON
{"active": "$active", "providers": {"sandbox": {"url": "http://127.0.0.1:8090/v1", "model": "",
 "context": ${OSYR_SANDBOX_CONTEXT:-16384}}}}
JSON
    )
fi
if [ -S /run/osyr/llama.sock ]; then
    # The benchmark: the model server itself, where the chat expects the local one
    python3 /run/osyr/relay.py 127.0.0.1 "${OSYR_LLAMA_PORT:-8083}" /run/osyr/llama.sock &
fi
[ $# -gt 0 ] && exec "$@"
exec osyr-chat
