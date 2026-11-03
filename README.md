# osyr

**OSYR** is an operating system with its own AI assistant that runs on the laptop itself, even without Internet, on ordinary 8 GB machines. This repository is its base: a Buildroot `BR2_EXTERNAL` tree for x86_64 that builds a bootable image (USB, BIOS and UEFI) and a VirtualBox appliance.

It is also the common base of **newos**, a voice-first interface (Wayland kiosk + WPE WebKit).

Website and downloads: [ccmai.org/osyr](https://ccmai.org/osyr/) · Help and keyboard shortcuts: [ccmai.org/osyr/help.html](https://ccmai.org/osyr/help.html)

## What it does

- **Local AI**: Qwen3.5-4B (with vision) on the CPU through llama.cpp, which picks the fastest code for each processor. The context size adapts to the RAM.
- **The assistant** runs the system through the `osyr` command line (JSON output): wifi, sound, screens, Bluetooth, brightness, temperature, power, reminders and updates. It asks before changing anything.
- **Voice**: hold Super+Space and speak (whisper.cpp); natural spoken answers with Piper voices (sherpa-onnx) in seven languages; dictation into any window.
- **Screen and clipboard**: `/look` shows the screen to the model; it can summarise or translate what you copied.
- **Optional remote providers** (your own server, OpenAI, Anthropic, Gemini, Ollama Cloud, OpenRouter, any OpenAI-compatible API). Keys are entered on your phone through an encrypted pairing page, never typed on screen.
- **Desktop**: sway + foot with a WarGames look, PipeWire sound, Bluetooth, several screens, screen lock, notifications.
- **Install to disk**: `sudo osyr-install` (erases the disk): system A and B, `/home` optionally encrypted with LUKS2. `osyr update image` puts a new signed image in the other slot, boots it on trial and goes back by itself if it does not start.
- **Signed updates**: `osyr update code` follows signed `v*` tags (most of them apply without rebooting), checks daily, notifies once per version and never installs on its own; exact rollback.
- **Support**: `osyr sys selftest` checks the hardware; `osyr-diag` sends logs and the self-test (your conversations only if you ask for it with `/report chats`).

## Status

Early test build, **v0.29.2** (2 Nov 2026). It boots and passes `make test` in QEMU (BIOS and UEFI) and has been tested on two HP laptops (2019 and 2014), installed to disk on the older one. Roadmap and decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); work log: [docs/MILESTONE-0.md](docs/MILESTONE-0.md).

## Requirements

- To run it: a 64-bit PC with 8 GB of RAM (16 GB recommended) and a 16 GB USB stick, or VirtualBox. Without AVX2 the local model is very slow: use a server model.
- To build it: Linux, ~60 GB of disk, and patience (about 1 h 20 min from scratch with 16 cores; much less with ccache).

## Build and test

```bash
git clone https://github.com/ccarrillomanzanares/osyr && cd osyr
make model        # Qwen3.5-4B Q4_K_M + vision projector + whisper model into models/ (~3.4 GB)
make              # clones Buildroot 2026.08.1 into build/buildroot the first time
make qemu         # or 'make qemu-uefi'; QEMU_DISPLAY=vnc=:1 on a headless server
make test         # native (image binaries on the host), vm (QEMU) and install (disk installer)
```

The image is written to `build/output/images/osyr.img`. Write it to a USB stick with `dd`, balenaEtcher or Rufus.

Publishing (maintainers): `scripts/publish.sh img` then `scripts/publish.sh vbox` (USB image, then VirtualBox appliance; both regenerate the website from `web/`), and `scripts/release.sh vX.Y.Z` to sign a release for `osyr update code`.

## Layout

| Path | Contents |
|---|---|
| `configs/osyr_x86_64_defconfig` | Buildroot configuration |
| `board/osyr/` | kernel fragment, GRUB, image layout, post-build and post-image scripts |
| `board/osyr/rootfs-overlay/` | everything OSYR adds: the `osyr` CLI (`usr/lib/osyr`), the chat and agent, session, services, themes, help |
| `package/` | extra packages (whisper.cpp, grim, swaylock, wl-clipboard...) |
| `tests/` | `make test` |
| `web/` | website templates for ccmai.org/osyr |
| `scripts/` | publishing, signed releases, model download, diagnostics receiver |

## Related projects

- [newos](https://github.com/ccarrillomanzanares/newos)

## License

MIT
