#!/bin/sh
# OSYR — post-image: builds osyr.img (USB and QEMU, BIOS + UEFI) with genimage.
# Runs from the Buildroot tree.
set -e
BOARD_DIR="$(dirname "$(readlink -f "$0")")"
ROOT_PARTUUID="a105a105-02"   # disk signature from genimage.cfg + partition 2

# EFI partition: Buildroot's GRUB EFI + kernel + our grub.cfg
ESP="$BINARIES_DIR/esp"
rm -rf "$ESP"
mkdir -p "$ESP/boot/grub"
cp -r "$BINARIES_DIR/efi-part/EFI" "$ESP/"
cp "$BINARIES_DIR/bzImage" "$ESP/boot/bzImage"
sed "s|@ROOT@|PARTUUID=$ROOT_PARTUUID|g" "$BOARD_DIR/grub.cfg" > "$ESP/boot/grub/grub.cfg"
# GRUB reads different paths depending on the firmware: BIOS -> /boot/grub/grub.cfg,
# UEFI -> /EFI/BOOT/grub.cfg. Buildroot's points at partition 1 (the FAT), which
# gives a black screen on UEFI (lesson from newos): ours is written to both.
cp "$ESP/boot/grub/grub.cfg" "$ESP/EFI/BOOT/grub.cfg"

# GRUB's BIOS first sector
if [ ! -f "$BINARIES_DIR/boot.img" ]; then
    for p in "$HOST_DIR/lib/grub/i386-pc/boot.img" "$TARGET_DIR/lib/grub/i386-pc/boot.img" \
             "$BUILD_DIR"/grub2-*/build-i386-pc/grub-core/boot.img; do
        [ -f "$p" ] && { cp -f "$p" "$BINARIES_DIR/boot.img"; break; }
    done
fi
[ -f "$BINARIES_DIR/boot.img" ] || { echo "post-image: GRUB boot.img missing"; exit 1; }

support/scripts/genimage.sh -c "$BOARD_DIR/genimage.cfg"

echo ""
echo "=== OSYR: image built ==="
echo "  $BINARIES_DIR/osyr.img"
echo "  QEMU:  make qemu"
echo "  USB:   sudo dd if=osyr.img of=/dev/sdX bs=4M status=progress conv=fsync"
