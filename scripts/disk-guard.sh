#!/bin/sh
# OSYR — frees disk before a build and refuses to start if there is still not enough.
# Run by 'make' before Buildroot (and by hand: 'make prune'). The disk filled up
# once mid-build (kernel with debug info: 25 GB); a full build/output is ~80 GB.
#
# What it deletes (only things that can be regenerated or are already published):
#   - build/output/build/<pkg>-<old version>: build dirs of packages that are no longer
#     in the configuration or changed version (Buildroot never deletes them)
#   - build/output/per-package/<pkg>: same, for packages no longer in the configuration
#   - build/osyr-<commit>.*: compressed images and .ova of versions that are neither
#     the published one nor the one in build/output/images
#   - build/prev-*: copies of earlier published versions, except the latest one
#   - build/web-prev-*: copies of earlier web pages, except the 3 latest
#   - build/vbox-*: work dirs left by an interrupted 'publish.sh vbox'
#
# Then it checks the free space: OSYR_MIN_FREE_GB (default 30 with an existing
# build/output, 100 for a build from scratch). 'make prune' only frees, no check.
set -e
ROOT="$(readlink -f "$(dirname "$0")/..")"
B="$ROOT/build"
OUT="$B/output"
RELEASES="${OSYR_RELEASES:-/var/www/ccmai.org/osyr/releases}"
freed=0

gone() {  # gone PATH...: deletes and counts what was freed
    for p in "$@"; do
        [ -e "$p" ] || continue
        kb=$(du -sk "$p" | cut -f1)
        rm -rf "$p"
        freed=$((freed + kb))
        echo "  deleted $(echo "$p" | sed "s|^$ROOT/||") ($((kb / 1024)) MB)"
    done
}

echo "disk-guard: freeing what is obsolete ..."

# 1. Package build dirs that the current configuration no longer uses
if [ -f "$OUT/.config" ] && [ -d "$OUT/build" ]; then
    info="$B/.show-info.json"
    make -s -C "$B/buildroot" O="$OUT" BR2_EXTERNAL="$ROOT" show-info > "$info"
    stale=$(python3 - "$info" "$OUT" <<'EOF'
import json, os, sys
info, out = json.load(open(sys.argv[1])), sys.argv[2]
# build_dir is "build/<pkg>-<version>[/subdir]": keep the first component after build/
used = {p["build_dir"].split("/")[1] for p in info.values()
        if p.get("build_dir", "").startswith("build/")}
names = set(info)
for d in sorted(os.listdir(os.path.join(out, "build"))):
    p = os.path.join(out, "build", d)
    # only real package dirs (Buildroot leaves .stamp_* in them), never buildroot-config etc.
    if d not in used and os.path.isdir(p) and any(f.startswith(".stamp_") for f in os.listdir(p)):
        print(p)
pp = os.path.join(out, "per-package")
if os.path.isdir(pp):
    for d in sorted(os.listdir(pp)):
        if d not in names:
            print(os.path.join(pp, d))
EOF
)
    rm -f "$info"
    # shellcheck disable=SC2086
    [ -z "$stale" ] || gone $stale
fi

# 2. Images of old versions in build/
pub=$(sed -n 's/^COMMIT=//p' "$RELEASES/osyr.img.xz.info" 2>/dev/null | cut -c1-7)
cur=$(sed -n 's/^COMMIT=//p' "$OUT/target/etc/osyr/build-info" 2>/dev/null | cut -c1-7)
for f in "$B"/osyr-*; do
    [ -e "$f" ] || continue
    c=$(basename "$f" | sed -E 's/^osyr-([0-9a-f]{7}).*/\1/')
    [ "$c" = "$pub" ] || [ "$c" = "$cur" ] || gone "$f"
done

# 3. Copies of earlier published versions: only the latest one (prev-COMMIT-NAME)
lastprev=$(ls -t "$B"/prev-* 2>/dev/null | head -1 | sed -E 's|.*/prev-([^-]+)-.*|\1|')
for f in "$B"/prev-*; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in "prev-$lastprev-"*) ;; *) gone "$f" ;; esac
done

# 4. Earlier web pages (keep 3) and interrupted VirtualBox conversions
# shellcheck disable=SC2046
gone $(ls -dt "$B"/web-prev-* 2>/dev/null | tail -n +4)
gone "$B"/vbox-*

echo "disk-guard: freed $((freed / 1024 / 1024)) GB"
[ "$1" = prune ] && exit 0

# 5. Enough free space?
if [ -d "$OUT/build" ]; then need=${OSYR_MIN_FREE_GB:-30}; else need=${OSYR_MIN_FREE_GB:-100}; fi
mkdir -p "$B"
free=$(df -Pk "$B" | awk 'NR==2 {print int($4 / 1024 / 1024)}')
if [ "$free" -lt "$need" ]; then
    echo "disk-guard: only $free GB free, the build needs at least $need GB." >&2
    echo "Largest items in build/ (OSYR_MIN_FREE_GB=N to change the limit):" >&2
    du -sh "$B"/* 2>/dev/null | sort -h | tail -6 >&2
    exit 1
fi
echo "disk-guard: $free GB free (minimum $need GB)"
