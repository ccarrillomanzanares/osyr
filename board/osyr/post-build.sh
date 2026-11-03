#!/bin/sh
# OSYR — post-build: runs on $TARGET_DIR before the images are generated.
set -e
BOARD_DIR="$(dirname "$(readlink -f "$0")")"
ROOT_DIR="$(readlink -f "$BOARD_DIR/../..")"

# --- Overlay scripts: LF first, THEN the executable bit ---------------------
# (lesson from newos: edited from Windows they arrive with CRLF and the shebang
#  fails; and "tr > f.lf && mv" creates a new 0644 file, so chmod goes last)
for f in "$TARGET_DIR"/usr/bin/osyr "$TARGET_DIR"/usr/bin/osyr-* \
         "$TARGET_DIR"/usr/lib/osyr/osyr/*.py "$TARGET_DIR"/home/osyr/.bash_profile; do
    [ -f "$f" ] || continue
    tr -d '\r' < "$f" > "$f.lf" && mv -f "$f.lf" "$f"
done
chmod 0755 "$TARGET_DIR"/usr/bin/osyr "$TARGET_DIR"/usr/bin/osyr-* "$TARGET_DIR"/usr/lib/osyr/osyr-*
chmod 0644 "$TARGET_DIR"/usr/lib/osyr/osyr/*.py
# .pyc from the working tree (ignored by git): stale in the image and not part of the commit
find "$TARGET_DIR"/usr/bin "$TARGET_DIR"/usr/lib/osyr -name __pycache__ -type d -prune -exec rm -rf {} +

# --- llama.cpp: CPU backends in a single place ------------------------------
# With GGML_BACKEND_DL the variants (libggml-cpu-haswell.so, -skylakex...) are
# installed next to the binaries; llama-server looks for them in /usr/lib/ggml.
mkdir -p "$TARGET_DIR/usr/lib/ggml"
find "$TARGET_DIR/usr" -path "$TARGET_DIR/usr/lib/ggml" -prune -o \
     -name 'libggml-cpu*.so' -print | while read -r so; do
    mv -f "$so" "$TARGET_DIR/usr/lib/ggml/"
done
n=$(find "$TARGET_DIR/usr/lib/ggml" -name 'libggml-cpu*.so' | wc -l)
echo "post-build: $n ggml CPU variants in /usr/lib/ggml"
[ "$n" -gt 0 ] || echo "post-build: WARNING: no CPU variants; check external.mk"

# --- Local model (milestone 0: inside the root) -----------------------------
# 'make model' leaves them in models/; here they are copied into the image if present.
mkdir -p "$TARGET_DIR/usr/share/osyr/models"
if ls "$ROOT_DIR"/models/*.gguf >/dev/null 2>&1; then
    cp -f "$ROOT_DIR"/models/*.gguf "$TARGET_DIR/usr/share/osyr/models/"
    echo "post-build: models copied: $(ls "$ROOT_DIR"/models/*.gguf | xargs -n1 basename | tr '\n' ' ')"
else
    echo "post-build: WARNING: no models in models/ (run 'make model')"
fi

# --- Code version the image is built from ----------------------------------
# 'osyr update code' starts from this commit to know what has changed.
{
    echo "COMMIT=$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    # new files without 'git add' too: Buildroot copies them anyway
    [ -z "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all 2>/dev/null)" ] && \
        git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1 && echo "DIRTY=no" || echo "DIRTY=yes"
    echo "DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$TARGET_DIR/etc/osyr/build-info"
echo "post-build: build-info $(head -1 "$TARGET_DIR/etc/osyr/build-info")"

# --- osyr-diag: receiver token ----------------------------------------------
# The token is not in git: it is read from local/diag.token if present. Owner
# and mode (root:osyr 0640) are set by permissions.txt; that is why the file
# always exists (empty if there is no token: osyr-diag treats it as "no upload").
if [ -f "$ROOT_DIR/local/diag.token" ]; then
    install -m 0640 "$ROOT_DIR/local/diag.token" "$TARGET_DIR/etc/osyr/diag.token"
    echo "post-build: osyr-diag token installed"
else
    : > "$TARGET_DIR/etc/osyr/diag.token"
    chmod 0640 "$TARGET_DIR/etc/osyr/diag.token"
    echo "post-build: WARNING: no local/diag.token, osyr-diag will not be able to upload"
fi

# --- Speech model (whisper.cpp) ------------------------------------------------
mkdir -p "$TARGET_DIR/usr/share/osyr/models/whisper"
if ls "$ROOT_DIR"/models/whisper/*.bin >/dev/null 2>&1; then
    cp -f "$ROOT_DIR"/models/whisper/*.bin "$TARGET_DIR/usr/share/osyr/models/whisper/"
    echo "post-build: speech model: $(ls "$ROOT_DIR"/models/whisper/*.bin | xargs -n1 basename)"
else
    echo "post-build: WARNING: no speech model in models/whisper (run 'make model')"
fi

# --- Voices for reading aloud (Piper, with sherpa-onnx) ------------------------
# 'make model' leaves them in models/piper/<language>/ (model.onnx, tokens.txt, sid) and
# models/piper/espeak-ng-data (one copy for all). Without them, voice.py uses espeak.
rm -rf "$TARGET_DIR/usr/share/osyr/models/piper"
if [ -d "$ROOT_DIR/models/piper/espeak-ng-data" ]; then
    mkdir -p "$TARGET_DIR/usr/share/osyr/models/piper"
    cp -a "$ROOT_DIR/models/piper/espeak-ng-data" "$TARGET_DIR/usr/share/osyr/models/piper/"
    for d in "$ROOT_DIR"/models/piper/*/; do
        [ -f "$d/model.onnx" ] || continue
        l=$(basename "$d")
        mkdir -p "$TARGET_DIR/usr/share/osyr/models/piper/$l"
        cp -f "$d/model.onnx" "$d/tokens.txt" "$d/sid" "$d/MODEL_CARD" "$TARGET_DIR/usr/share/osyr/models/piper/$l/"
    done
    echo "post-build: voices: $(cat "$ROOT_DIR"/models/piper/*/voice 2>/dev/null | tr '\n' ' ')"
else
    echo "post-build: WARNING: no voices in models/piper (run 'make model'): espeak will be used"
fi

# --- Sound: PipeWire for every application ------------------------------------
# User services (started by systemd --user when osyr logs in): PipeWire,
# its PulseAudio server (pipewire-pulse) and WirePlumber.
USER_UNITS="$TARGET_DIR/etc/systemd/user"
mkdir -p "$USER_UNITS/sockets.target.wants" "$USER_UNITS/default.target.wants" \
         "$USER_UNITS/pipewire.service.wants"
for u in pipewire.socket pipewire-pulse.socket; do
    [ -f "$TARGET_DIR/usr/lib/systemd/user/$u" ] && \
        ln -sf "/usr/lib/systemd/user/$u" "$USER_UNITS/sockets.target.wants/$u"
done
for u in pipewire.service pipewire-pulse.service; do
    [ -f "$TARGET_DIR/usr/lib/systemd/user/$u" ] && \
        ln -sf "/usr/lib/systemd/user/$u" "$USER_UNITS/default.target.wants/$u"
done
if [ -f "$TARGET_DIR/usr/lib/systemd/user/wireplumber.service" ]; then
    ln -sf /usr/lib/systemd/user/wireplumber.service "$USER_UNITS/pipewire.service.wants/wireplumber.service"
    ln -sf /usr/lib/systemd/user/wireplumber.service "$USER_UNITS/pipewire-session-manager.service"
fi
# The pulseaudio package only provides libpulse (loaded by Firefox, SDL...): its
# daemon must never start, the PulseAudio server is pipewire-pulse.
for u in pulseaudio.service pulseaudio.socket; do
    ln -sf /dev/null "$USER_UNITS/$u"
done
if [ -f "$TARGET_DIR/etc/pulse/client.conf" ]; then
    sed -i -E 's/^;? *autospawn *=.*/autospawn = no/' "$TARGET_DIR/etc/pulse/client.conf"
    grep -q '^autospawn = no' "$TARGET_DIR/etc/pulse/client.conf" || \
        echo 'autospawn = no' >> "$TARGET_DIR/etc/pulse/client.conf"
fi
# ALSA: the "default" device is PipeWire (aplay, SDL games, etc.)
mkdir -p "$TARGET_DIR/etc/alsa/conf.d"
for f in 50-pipewire.conf 99-pipewire-default.conf; do
    for d in usr/share/alsa/alsa.conf.d usr/share/pipewire/alsa-conf; do
        if [ -f "$TARGET_DIR/$d/$f" ]; then
            ln -sf "/$d/$f" "$TARGET_DIR/etc/alsa/conf.d/$f"
            break
        fi
    done
done
echo "post-build: sound: $(ls "$TARGET_DIR/etc/alsa/conf.d" 2>/dev/null | tr '\n' ' ')"

# --- Services -----------------------------------------------------------------
WANTS="$TARGET_DIR/etc/systemd/system/multi-user.target.wants"
mkdir -p "$WANTS"
ln -sf /etc/systemd/system/osyr-llama.service "$WANTS/osyr-llama.service"
for unit in iwd.service systemd-networkd.service systemd-resolved.service bluetooth.service; do
    for d in usr/lib/systemd/system lib/systemd/system; do
        if [ -f "$TARGET_DIR/$d/$unit" ]; then
            ln -sf "/$d/$unit" "$WANTS/$unit"
            break
        fi
    done
done
# resolv.conf managed by systemd-resolved
ln -sf ../run/systemd/resolve/stub-resolv.conf "$TARGET_DIR/etc/resolv.conf"

# --- SSH: disabled (preset 10-osyr.preset) and, if enabled, keys only
if [ -f "$TARGET_DIR/etc/ssh/sshd_config" ]; then
    sed -i -E 's/^#?(PasswordAuthentication|KbdInteractiveAuthentication) .*/\1 no/; s/^#?PermitRootLogin .*/PermitRootLogin no/' \
        "$TARGET_DIR/etc/ssh/sshd_config"
fi

# --- sudo: the wheel group administers (with password) ----------------------
if [ -f "$TARGET_DIR/etc/sudoers.d/wheel" ]; then
    chmod 0440 "$TARGET_DIR"/etc/sudoers.d/*
    grep -qE '^[@#]includedir /etc/sudoers.d' "$TARGET_DIR/etc/sudoers" 2>/dev/null || \
        echo '@includedir /etc/sudoers.d' >> "$TARGET_DIR/etc/sudoers"
fi

# --- GRUB ----------------------------------------------------------------------
# Buildroot's generic grub.cfg in the root would confuse the embedded config's
# search for /boot/grub/grub.cfg (lesson from newos).
rm -f "$TARGET_DIR/boot/grub/grub.cfg"

# --- GRUB for the internal disk (osyr-install) --------------------------------
# Separate cores for the disk: they look for /osyr/grub/grub.cfg instead of the
# USB's /boot/grub/grub.cfg, so with both plugged in each one boots its own
# system. They include loadenv: grubenv picks root A or B.
BOOT_OUT="$TARGET_DIR/usr/lib/osyr/boot"
GRUB_BUILD="$(ls -d "$BUILD_DIR"/grub2-*/ | grep -v host- | head -1)"
MODS="boot linux ext2 fat part_msdos part_gpt normal search search_fs_file search_fs_uuid test loadenv"
mkdir -p "$BOOT_OUT"
"$HOST_DIR/bin/grub-mkimage" -d "$GRUB_BUILD/build-i386-pc/grub-core" -O i386-pc \
    -o "$BOOT_OUT/core-pc.img" -p /osyr/grub -c "$BOARD_DIR/grub-embed-disk.cfg" $MODS biosdisk
"$HOST_DIR/bin/grub-mkimage" -d "$GRUB_BUILD/build-x86_64-efi/grub-core" -O x86_64-efi \
    -o "$BOOT_OUT/bootx64.efi" -p /osyr/grub -c "$BOARD_DIR/grub-embed-disk.cfg" $MODS efi_gop
cp -f "$GRUB_BUILD/build-i386-pc/grub-core/boot.img" "$BOOT_OUT/boot.img"
echo "post-build: disk GRUB: $(ls "$BOOT_OUT" | tr '\n' ' ')"
