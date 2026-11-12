#!/bin/sh
# OSYR — downloads the milestone 0 local model to models/ (outside git).
# post-build.sh copies models/*.gguf to /usr/share/osyr/models/ in the image.
# Usage: scripts/download-model.sh [quantization]   (Q4_K_M by default)
set -e
REPO="unsloth/Qwen3.5-4B-GGUF"
QUANT="${1:-Q4_K_M}"
DEST="$(dirname "$(readlink -f "$0")")/../models"
URL="https://huggingface.co/$REPO/resolve/main"
mkdir -p "$DEST"

# get <url> <local path relative to models/> [sha256]: with sha256 the download is checked
# (Hugging Face ones come from the file's x-linked-etag header). What is already
# there is not checked again: the full check is 'make test' on the image.
get() {
    if [ -s "$DEST/$2" ]; then
        echo "already there: models/$2"
        return
    fi
    echo "downloading ${1##*/} -> models/$2"
    curl -fL --retry 3 -C - -o "$DEST/$2.part" "$1"
    if [ -n "$3" ]; then
        echo "$3  $DEST/$2.part" | sha256sum -c - >/dev/null || \
            { echo "sha256 mismatch: models/$2 (corrupt download or file changed upstream)" >&2; rm -f "$DEST/$2.part"; exit 1; }
    fi
    mv -f "$DEST/$2.part" "$DEST/$2"
}

# Only the default quantization has a recorded sha256
qsum=
[ "$QUANT" = Q4_K_M ] && qsum=00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4
get "$URL/Qwen3.5-4B-$QUANT.gguf" "Qwen3.5-4B-$QUANT.gguf" "$qsum"
# The repo's mmproj has the same name for every model: it is renamed so that
# /usr/share/osyr/models shows which model it belongs to.
get "$URL/mmproj-F16.gguf" "Qwen3.5-4B-mmproj-F16.gguf" cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864
# Speech recognition (whisper.cpp): quantized multilingual small, ~190 MB.
# Good Spanish on the CPU; post-build.sh copies it to /usr/share/osyr/models/whisper.
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
mkdir -p "$DEST/whisper"
get "$WHISPER_URL/ggml-small-q5_1.bin" "whisper/ggml-small-q5_1.bin" ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb

# Voices for reading aloud (Piper, packaged for sherpa-onnx): one per language
# offered at first boot, LANGUAGE:VOICE:SPEAKER:SHA256 of the .tar.bz2. Only
# licenses that allow any use: non-commercial ones are out (lessac and what was
# fine-tuned from it: amy, paola; ryan, hfc). Chosen by the user: es (sharvard,
# voice 2) and en (libritts_r, voice 0); the rest, the best free one for each
# language (it: only x_low exists; pt: Brazilian only).
PIPER_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
PIPER_VOICES="
es:es_ES-sharvard-medium:1:b30a7a83df0518f0ee1c7039506648cade99f1f9b498fc49ed2ced2e2536bb5a
en:en_US-libritts_r-medium:0:10dc268f3e371696d721486123e2705a9fc1faa113491979fde4d88dba1f1b1c
ca:ca_ES-upc_ona-medium:0:b6c50f22e13cfc72ce51c496d22b987adf6704047f6d342968197b4b0b3127a5
fr:fr_FR-siwis-medium:0:375909aa30842b3a4efa10b1beb1d761af792960ae6873b4d53889f96c66195b
de:de_DE-thorsten-medium:0:50487d9c95fdf2191f31d2588569381063ba1591dcd4c7d4bdd30f12b2191714
it:it_IT-riccardo-x_low:0:8ad0791558ae2b3e96054fc5756ae3ae2f6457fb1765bc01f43bbab85e0a7021
pt:pt_BR-faber-medium:0:7add3f923ad6bc25ca8a192805fd1a64d1b3893e4611c4a9719545a825039a83
"
mkdir -p "$DEST/piper"
for v in $PIPER_VOICES; do
    lang=${v%%:*}; rest=${v#*:}; name=${rest%%:*}; rest=${rest#*:}; sid=${rest%%:*}; sum=${rest#*:}
    if [ "$(cat "$DEST/piper/$lang/voice" 2>/dev/null)" = "$name:$sid" ]; then
        echo "already there: models/piper/$lang ($name)"
        continue
    fi
    echo "downloading voice $name -> models/piper/$lang"
    tmp="$DEST/piper/.$name.tar.bz2"
    curl -fL --retry 3 -C - -o "$tmp" "$PIPER_URL/vits-piper-$name.tar.bz2"
    echo "$sum  $tmp" | sha256sum -c - >/dev/null || { echo "sha256 mismatch: $name" >&2; rm -f "$tmp"; exit 1; }
    x="$DEST/piper/.x"; rm -rf "$x"; mkdir -p "$x"
    tar -xjf "$tmp" -C "$x"
    rm -rf "$DEST/piper/$lang"; mkdir -p "$DEST/piper/$lang"
    mv "$x/vits-piper-$name/$name.onnx" "$DEST/piper/$lang/model.onnx"
    mv "$x/vits-piper-$name/tokens.txt" "$x/vits-piper-$name/MODEL_CARD" "$DEST/piper/$lang/"
    [ -d "$DEST/piper/espeak-ng-data" ] || mv "$x/vits-piper-$name/espeak-ng-data" "$DEST/piper/"
    echo "$sid" > "$DEST/piper/$lang/sid"
    echo "$name:$sid" > "$DEST/piper/$lang/voice"
    rm -rf "$x" "$tmp"
done
ls -lh "$DEST"/*.gguf "$DEST"/whisper/*.bin "$DEST"/piper/*/model.onnx
