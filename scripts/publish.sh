#!/bin/sh
# OSYR — publishes the image at https://ccmai.org/osyr/releases/, in two phases:
#
#   scripts/publish.sh img [vX.Y.Z]
#                             osyr.img -> osyr.img.xz (+ .sha256): USB, BIOS and UEFI.
#                             With vX.Y.Z it signs that tag on the image's commit
#                             (scripts/release.sh); without it, the commit must already have one.
#                             The changelog (web/changelog.html) must have its entry.
#   scripts/publish.sh vbox   osyr.img -> osyr.ova (+ .sha256): VirtualBox VM ready to
#                             import; its disk is booted in QEMU before publishing
#   scripts/publish.sh web    the website (ccmai.org/osyr) from the web/ templates with
#                             what is published in releases/; "img" and "vbox" regenerate it at the end
#   scripts/publish.sh web preview   the same, but only into build/web-preview
#
# The two phases are separate: after "img" the user is told (and downloads the
# image to try it) and then "vbox" is done. Both start from
# build/output/images/osyr.img, which must come from a clean commit (DIRTY=no) and
# have passed 'make test'. The previously published version is kept in build/.
# Before publishing, main and the tag are pushed (without the tag on GitHub,
# 'osyr update code' on installed machines does not see it); if that fails, nothing is published.
# Needs sudo (releases/ belongs to root) and VirtualBox for "vbox".
set -e
ROOT="$(readlink -f "$(dirname "$0")/..")"
IMG="$ROOT/build/output/images/osyr.img"
INFO="$ROOT/build/output/target/etc/osyr/build-info"
RELEASES="${OSYR_RELEASES:-/var/www/ccmai.org/osyr/releases}"
URL="https://ccmai.org/osyr/releases"

WEB="$(dirname "$RELEASES")"

# web [preview]: web/ templates -> build/web-preview, and unless preview, to the website
# (copy of the previous pages in build/web-prev-DATE)
web() {
    out="$ROOT/build/web-preview"
    rm -rf "$out"
    python3 "$ROOT/scripts/render-web.py" "$RELEASES" "$out"
    [ "$1" = preview ] && { echo "Draft in $out"; return 0; }
    prev="$ROOT/build/web-prev-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$prev/releases"
    for page in index.html about.html help.html changelog.html releases/index.html og.png; do
        [ -f "$WEB/$page" ] && cp "$WEB/$page" "$prev/$page"
        sudo install -m 0644 "$out/$page" "$WEB/$page"
    done
    # front page logos (web/assets, in git)
    sudo install -d -m 0755 "$WEB/assets"
    sudo install -m 0644 "$ROOT"/web/assets/* "$WEB/assets/"
    pub=$(sed -n 's/^COMMIT=//p' "$RELEASES/osyr.img.xz.info")
    # (with no version, grep -q "" would accept any page)
    shown=$(git -C "$ROOT" describe --tags --abbrev=0 --match 'v*' "$pub" 2>/dev/null) && [ -n "$shown" ] || \
        { echo "ERROR: cannot tell which version is published (commit '$pub' has no tag)" >&2; exit 1; }
    curl -fsS https://ccmai.org/osyr/ | grep -q "$shown" || \
        { echo "ERROR: the website does not show the new version" >&2; exit 1; }
    echo "WEBSITE UPDATED https://ccmai.org/osyr/ (previous in $prev)"
}

if [ "$1" = web ]; then
    web "$2"
    exit 0
fi

[ -f "$IMG" ] && [ -f "$INFO" ] || { echo "No image: run 'make'" >&2; exit 1; }
COMMIT=$(sed -n 's/^COMMIT=//p' "$INFO" | cut -c1-7)
if ! grep -q '^DIRTY=no' "$INFO" && [ "$FORCE" != 1 ]; then
    echo "The image comes from a tree with uncommitted changes (DIRTY=yes). FORCE=1 to publish it anyway." >&2
    exit 1
fi

# Version: signed v* tag on the image's commit, with a changelog entry
FULL=$(sed -n 's/^COMMIT=//p' "$INFO")
TAG=$(git -C "$ROOT" tag --points-at "$FULL" -l 'v*' --sort=-v:refname | head -1)
WANT="$2"
case "$1" in img|vbox)
    if [ -n "$WANT" ] && [ -n "$TAG" ] && [ "$WANT" != "$TAG" ]; then
        echo "The image ($COMMIT) is already $TAG, not $WANT" >&2; exit 1
    fi
    if [ -z "$TAG" ] && [ -z "$WANT" ]; then
        last=$(git -C "$ROOT" describe --tags --abbrev=0 --match 'v*' "$FULL" 2>/dev/null)
        echo "The image ($COMMIT) has no version (the latest is ${last:-none}): $0 $1 vX.Y.Z" >&2; exit 1
    fi
    v="${TAG:-$WANT}"
    grep -q "<h2>$v " "$ROOT/web/changelog.html" || \
        { echo "web/changelog.html has no entry for $v: write it and commit" >&2; exit 1; }
    if [ -z "$TAG" ]; then
        "$ROOT/scripts/release.sh" "$WANT" "OSYR $WANT" "$FULL"
        TAG="$WANT"
    else
        git -C "$ROOT" -c gpg.format=ssh \
            -c gpg.ssh.allowedSignersFile="$ROOT/board/osyr/rootfs-overlay/etc/osyr/update-signers" \
            verify-tag "$TAG" >/dev/null 2>&1 || { echo "Tag $TAG is not signed with the OSYR key" >&2; exit 1; }
    fi
    ;;
esac

# Push main and the tag. The VPS has no ~/.gitconfig: credentials come from
# ~/.git-credentials (HTTPS) with credential.helper=store.
case "$1" in img|vbox)
    [ "$(git -C "$ROOT" branch --show-current)" = main ] || { echo "Publish from the main branch" >&2; exit 1; }
    git -C "$ROOT" merge-base --is-ancestor "$FULL" main || \
        { echo "The image commit ($COMMIT) is not on main" >&2; exit 1; }
    git -C "$ROOT" -c credential.helper=store push origin main "$TAG" || \
        { echo "Pushing main and $TAG failed: nothing is published" >&2; exit 1; }
    ;;
esac

# publish FILE NAME: copies to releases/ with its .sha256, keeping the previous one first
publish() {
    src="$1"; name="$2"
    if [ -f "$RELEASES/$name" ]; then
        old=$(sed -n 's/^COMMIT=//p' "$RELEASES/$name.info" 2>/dev/null | cut -c1-7)
        keep="$ROOT/build/prev-${old:-unknown}-$name"
        [ -f "$keep" ] || sudo cat "$RELEASES/$name" > "$keep"
        echo "Previous one kept in $keep"
    fi
    # copy under another name and mv: a downloader never sees a half-written file
    # 0644: VBoxManage creates the .ova as 0600 and the website could not serve it
    sudo install -m 0644 "$src" "$RELEASES/.$name.new"
    sudo mv "$RELEASES/.$name.new" "$RELEASES/$name"
    (cd "$(dirname "$src")" && sha256sum "$(basename "$src")") | sed "s|  .*|  $name|" | \
        sudo tee "$RELEASES/$name.sha256" >/dev/null
    sudo install -m 0644 "$INFO" "$RELEASES/$name.info"
    # check from outside: what the website serves is what was published
    remote=$(curl -fsS "$URL/$name.sha256" | cut -d' ' -f1)
    local_sum=$(sha256sum "$src" | cut -d' ' -f1)
    [ "$remote" = "$local_sum" ] || { echo "ERROR: the website serves a different sha256 for $name" >&2; exit 1; }
    size=$(curl -fsSI "$URL/$name" | tr -d '\r' | sed -n 's/^content-length: //Ip')
    echo "PUBLISHED $URL/$name  ($size bytes, commit $COMMIT)"
    echo "sha256 $local_sum"
}

# manifest XZ: osyr.img.xz.manifest and .sig (checked by 'osyr update image') and
# osyr-update.img.xz: the root without the models (~400 MB), for machines that already
# have them. Generated, and the signature verified, BEFORE publishing anything: if it
# fails (no key, debugfs...), the website stays intact with the previous version.
manifest_build() {
    [ -n "$TAG" ] || { echo "No version: no manifest" >&2; exit 1; }
    m="$ROOT/build/osyr-$COMMIT.img.xz.manifest"
    upd="$ROOT/build/osyr-$COMMIT-update.img.xz"
    # OSYR_UPDATE_KEEP: models new in this version (e.g. usr/share/osyr/models/piper)
    python3 "$ROOT/scripts/image-manifest.py" "$IMG" "$1" "$TAG" "$FULL" "$m" --update "$upd" \
        ${OSYR_UPDATE_KEEP:+--keep "$OSYR_UPDATE_KEEP"}
    ssh-keygen -Y verify -f "$ROOT/board/osyr/rootfs-overlay/etc/osyr/update-signers" \
        -I osyr-release@ccmai.org -n osyr-image -s "$m.sig" < "$m" >/dev/null || \
        { echo "The manifest does not verify against update-signers: nothing is published" >&2; exit 1; }
}

# The manifest goes last: whoever reads it already finds both images it names
manifest_publish() {
    for f in "$m" "$m.sig"; do
        sudo install -m 0644 "$f" "$RELEASES/.new"
        sudo mv "$RELEASES/.new" "$RELEASES/osyr.img.xz.${f##*.img.xz.}"
    done
    curl -fsS "$URL/osyr.img.xz.manifest" | cmp -s - "$m" || { echo "ERROR: the website serves a different manifest" >&2; exit 1; }
    echo "PUBLISHED $URL/osyr.img.xz.manifest (+ .sig, $TAG)"
}

case "$1" in
img)
    out="$ROOT/build/osyr-$COMMIT.img.xz"
    if [ ! -f "$out" ] || [ "$IMG" -nt "$out" ]; then
        echo "Compressing $IMG ..."
        xz -T0 -6 -k -c "$IMG" > "$out.tmp"
        mv "$out.tmp" "$out"
    fi
    xz -t "$out"
    manifest_build "$out"
    publish "$upd" osyr-update.img.xz
    publish "$out" osyr.img.xz
    manifest_publish
    web
    ;;
manifest)
    # the manifest of the already published image (e.g. one from before 'osyr update image')
    out="$ROOT/build/osyr-$COMMIT.img.xz"
    [ -f "$out" ] || { echo "Missing $out" >&2; exit 1; }
    [ "$(sha256sum "$out" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$RELEASES/osyr.img.xz.sha256")" ] || \
        { echo "$out is not the published image" >&2; exit 1; }
    manifest_build "$out"
    publish "$upd" osyr-update.img.xz
    manifest_publish
    ;;
vbox)
    command -v VBoxManage >/dev/null || { echo "VirtualBox (VBoxManage) is missing" >&2; exit 1; }
    # The .ova must be from the same image as osyr.img.xz: first "img", then "vbox"
    pubc=$(sudo sed -n 's/^COMMIT=//p' "$RELEASES/osyr.img.xz.info" 2>/dev/null || true)
    [ "$pubc" = "$FULL" ] || \
        { echo "published osyr.img.xz is ${pubc:-none}, not $COMMIT: publish first with '$0 img'" >&2; exit 1; }
    out="$ROOT/build/osyr-$COMMIT.ova"
    work="$ROOT/build/vbox-$COMMIT"
    vm="osyr-export-$COMMIT"
    # already converted and booted (osyr-COMMIT.ova.ok): only publishing is left
    if [ -f "$out.ok" ] && [ "$out.ok" -nt "$IMG" ]; then
        publish "$out" osyr.ova
        web
        exit 0
    fi
    rm -f "$out.ok"
    rm -rf "$work"
    mkdir -p "$work"
    # remove any VM left from a previous attempt
    VBoxManage unregistervm "$vm" --delete >/dev/null 2>&1 || true
    echo "Converting to VDI ..."
    VBoxManage convertfromraw "$IMG" "$work/osyr.vdi" --format VDI
    VBoxManage createvm --name "$vm" --ostype Linux_64 --basefolder "$work" --register
    # 8 GB and 4 CPUs: OSYR's minimum profile (4B local model). BIOS: the image
    # boots on UEFI too, but this way the VM does not depend on VirtualBox's EFI firmware.
    VBoxManage modifyvm "$vm" --memory 8192 --cpus 4 --firmware bios \
        --graphicscontroller vmsvga --vram 128 --rtc-use-utc on \
        --nic1 nat --nic-type1 virtio --audio-enabled on --audio-controller hda \
        --audio-out on --audio-in on --usb-ohci on --mouse usbtablet
    VBoxManage storagectl "$vm" --name SATA --add sata --controller IntelAhci --portcount 1 --bootable on
    VBoxManage storageattach "$vm" --storagectl SATA --port 0 --device 0 --type hdd --medium "$work/osyr.vdi"
    rm -f "$out.tmp.ova"
    VBoxManage export "$vm" --output "$out.tmp.ova" --vsys 0 --product OSYR \
        --producturl https://ccmai.org/osyr --version "$COMMIT" \
        --description "OSYR: an operating system with its own local AI assistant. Login: osyr / osyr (changed at first start)."
    VBoxManage unregistervm "$vm" --delete
    mv "$out.tmp.ova" "$out"
    # Before publishing: the disk inside the .ova boots (QEMU reads its VMDK)
    vmdk=$(tar -tf "$out" | grep '\.vmdk$' | head -1)
    tar -xf "$out" -C "$work" "$vmdk"
    python3 "$ROOT/tests/run.py" boot "$work/$vmdk" vmdk
    rm -rf "$work"
    touch "$out.ok"
    publish "$out" osyr.ova
    web
    ;;
*)
    echo "Usage: $0 img [vX.Y.Z] | vbox | web [preview]" >&2
    exit 2
    ;;
esac
