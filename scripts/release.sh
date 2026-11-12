#!/bin/sh
# OSYR — publishes a version for 'osyr update code': a tag signed with the
# key in ~/.ssh/osyr-release (its public part is in
# board/osyr/rootfs-overlay/etc/osyr/update-signers). Does not push: prints the command.
# Usage: scripts/release.sh v0.1.0 ["message"] [commit]   (HEAD by default)
# scripts/publish.sh img vX.Y.Z calls it with the image's commit.
set -e
TAG="$1"; MSG="${2:-OSYR $1}"; REV="${3:-HEAD}"
KEY="${OSYR_RELEASE_KEY:-$HOME/.ssh/osyr-release}"
ROOT="$(dirname "$(readlink -f "$0")")/.."
case "$TAG" in v[0-9]*) ;; *) echo "usage: $0 vX.Y.Z [message] [commit]" >&2; exit 2 ;; esac
[ -f "$KEY" ] || { echo "key not found: $KEY" >&2; exit 1; }
[ "$REV" != HEAD ] || git -C "$ROOT" diff --quiet HEAD || { echo "there are uncommitted changes" >&2; exit 1; }
# no ~/.gitconfig (the VPS): sign as the commit's author
git -C "$ROOT" config user.name >/dev/null || {
    GIT_COMMITTER_NAME=$(git -C "$ROOT" log -1 --format=%an "$REV")
    GIT_COMMITTER_EMAIL=$(git -C "$ROOT" log -1 --format=%ae "$REV")
    export GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
}
git -C "$ROOT" -c gpg.format=ssh -c user.signingkey="$KEY.pub" tag -s "$TAG" -m "$MSG" "$REV"
git -C "$ROOT" -c gpg.format=ssh \
    -c gpg.ssh.allowedSignersFile="$ROOT/board/osyr/rootfs-overlay/etc/osyr/update-signers" \
    verify-tag "$TAG"
echo "Signed $TAG. To publish it: git push origin main $TAG"
