# Milestone 0 — The base boots

**Gate: passed** (QEMU on 28 Oct, HP 15s on 29 Oct, installed on the HP 15-ba disk on 31 Oct). The image boots in QEMU and on both HPs, goes straight into a sway session with the WarGames theme, and `llama-server` serves Qwen3.5-4B on `127.0.0.1:8083`. The milestone stayed open for the installer, the updates and the hardware tests.

Full architecture: [ARCHITECTURE.md](ARCHITECTURE.md).

## Status

| Piece | Status |
| --- | --- |
| `external.desc`, `external.mk`, `Config.in` | Done. `external.mk` enables all the llama.cpp CPU variants |
| `configs/osyr_x86_64_defconfig` | Done, not built yet (Buildroot 2026.08.1); added `SYSTEMD_VCONSOLE` |
| Kernel 6.18.10: newos base + `board/osyr/kernel/osyr.fragment` | Done |
| GRUB BIOS + UEFI, `genimage.cfg`, `post-build.sh`, `post-image.sh` | Done (adapted from newos) |
| `osyr` user and autologin on tty1 | Done |
| Session: `.bash_profile` → `osyr-session` → sway + foot | Done. Without a `renderD*` node it uses `WLR_RENDERER=pixman`; if there is a node but mesa has no driver (Intel without iris) and sway cannot create the renderer, it retries with pixman (EGL log in `sway-egl.log`); if sway fails it leaves a shell on tty1 and the log in `~/.local/state/osyr/sway.log` |
| WarGames theme for sway and foot | Done: `/etc/sway/config` (shortcuts carried over from the previous system), `/etc/osyr/theme/sway.conf`, `/etc/xdg/foot/foot.ini` (`[colors-dark]`, foot 1.26), `osyr-status` bar |
| `osyr-llama.service` + `/etc/osyr/llama.conf` + `osyr-llama` | Done. `DynamicUser`, threads = physical cores, `-np 1 --jinja --mmproj`; autodetects the model if the configured one is missing |
| `osyr-chat` (minimal chat against llama-server, WarGames greeting) | Done, standard library only; SSE streaming, no reasoning (`enable_thinking: false`). Tested against a mock server |
| Network: `systemd-networkd` (wired) + `iwd` (wifi) | Done: `20-wired.network`, `25-wireless.network`; iwd only associates (`EnableNetworkConfiguration=false`) |
| `/etc/sudoers.d/wheel`, `/etc/locale.conf`, `/etc/vconsole.conf` (es) | Done |
| `Makefile` (`make`, `make model`, `make qemu`, `make qemu-uefi`) and `scripts/download-model.sh` | Done. The `mmproj` is saved as `Qwen3.5-4B-mmproj-F16.gguf` |
| First build and boot in QEMU | **Done (28 Oct).** Build from scratch: ~1 h 20 min the first time on the VPS (16 vCPU), 27 min with ccache. In QEMU, BIOS and UEFI: GRUB → kernel → systemd → autologin → sway + foot with the WarGames theme → `osyr-chat` answers in Spanish with streaming. `llama-server` loads Qwen3.5-4B + mmproj in ~30 s; ggml picks the CPU variant by itself; ~3.1 GB of RAM used with the model loaded |
| Fixes that came up at boot | PAM (`linux-pam`, util-linux `login`, `/etc/pam.d/login` with `pam_systemd`): without it there was no logind session and sway had no seat. `osyr` uid/gid fixed at 1000 (in mkusers `-1` is a system uid → `user-light` session without `systemd --user`). Session `LANG` from `locale.conf` with `pam_env` |
| `osyr-diag`: upload logs for support | Done. Collects system, hardware, graphics, sway logs, journal (osyr is in `systemd-journal`), llama and network (without wifi keys) and uploads a `.tar.gz` to `https://ccmai.org/osyr-diag/upload`; `--local` only saves it in `/tmp`. Token in `local/diag.token` (outside git) → `/etc/osyr/diag.token`. Receiver: `scripts/diag-server.py` + `scripts/osyr-diag-receiver.service` (127.0.0.1:8091, behind Apache), saves into `~/osyr-diag/` on the VPS. Tested inside the image in QEMU. Receiver running on the VPS (29 Oct): `osyr-diag-receiver` service and `ProxyPass /osyr-diag/` in `ccmai.org-ssl.conf` (previous copy in `.bak-20261029`); tested over HTTPS |
| GPU acceleration | Done (29 Oct), builds and boots in QEMU; not tested on real hardware. mesa 26.1.8 + LLVM 22: iris + crocus (Intel), radeonsi + r600 (AMD), nouveau, virgl, llvmpipe; `xe` firmware added. Build from scratch: 81 min. `osyr-gpu-probe` opens the render node of the main GPU with GBM+EGL and reads the driver (`eglGetDisplayDriverName`): if it is software (`kms_swrast`) or does not initialise, `osyr-session` uses pixman; result in `~/.local/state/osyr/gpu.log`. The retry based on the sway log stays as a second safety net |
| Accents on the es keyboard | `/usr/share/X11/locale` with the libX11 1.8.13 Compose table: without it xkbcommon had no dead keys and "á" could not be typed in foot |
| `osyr-diag` measures the model speed | `bench.txt`: one 64-token answer with the llama-server `timings` (prompt and generation tokens/s) |
| `osyr` CLI (preview of milestone 1) | Python package in `/usr/lib/osyr/osyr`, JSON contract (`--json` in any position; usage and internal errors also in JSON). `osyr net status`, `osyr net wifi scan/connect/disconnect` (iwd over D-Bus with `busctl --json`; `iwctl` only to hand over the password) and `osyr ui brightness [up/down/N]` (logind `SetBrightness`, minimum 5 %) |
| Bar and laptop keys | `osyr-status` in Python, a single process: model, network (SSID or ETH), battery, RAM and time. Brightness keys in sway |
| Chat: context and speed | `osyr-chat` reads `n_ctx` from `/props`, forgets the oldest turns above 75 % (estimate calibrated with the server `usage`) and, if llama-server still rejects for context, trims and retries. tok/s from the server `timings`. Tested against the image's llama-server natively with a 1024 context |
| `osyr update code` | `check / apply / rollback` from git (`/etc/osyr/update.conf`, by default the GitHub repo, main branch). Overlay only; warns about what needs a new image. Per-file three-way merge (base = commit in `/etc/osyr/build-info`): respects local changes and leaves `.osyr-new` if they clash. Backup of everything in `/var/lib/osyr/updates/` and exact `rollback`. `apply` asks for a token (`--confirm`) or a yes in the terminal. Needs root. Tested with a fake remote and root: rollback identical byte for byte |
| `osyr ai` and external providers | `status / list / add / use / remove`. OpenAI-compatible API; key in `Authorization: Bearer` or in another header (`--key-header X-API-Key`, like the VPS server). Context: `/props` (llama.cpp), `/v1/models`, model table, or asked (`--context`). `use` of an external one requires confirmation (data leaves the machine). `~/.config/osyr/ai.json` at 0600. The chat uses the active provider, falls back to the local one if the remote does not answer, and the bar shows `AI REMOTE <name>` |
| `CTX=auto` in `llama.conf` | `osyr-llama` computes the context from the GGUF metadata (`osyr/gguf.py`: training context and KV cache per token; in Qwen3.5 only 8 of 32 layers have KV, 32 KiB/token) and the RAM: RAM − 1.5 GiB − weights − 0.5 GiB, capped by CPU speed (16K with < 12 GiB, 32K with more). 8 GB → 16K (same as before); 12 GB or more → 32K; little RAM → less. If it fails, 16384. Validated against llama-server: KV 512 MiB at 16K and 2048 MiB at 64K (exactly 32 KiB/token), fixed compute buffer ~500 MiB, recurrent state 50 MiB. Note: on CPUs with AVX2 llama.cpp repacks the weights (`CPU_REPACK`, 1.3 GB anonymous) |
| SSH closed by default | `sshd` came enabled (Buildroot preset) with the user `osyr`/`osyr` in wheel: anyone on the same network got in as administrator. Now `10-osyr.preset` disables it and `sshd_config` only accepts keys (no password, no root). Host keys are generated on first boot. systemd-resolved LLMNR and mDNS disabled (port 5355 open to the network) |
| Sound for every application | PipeWire + WirePlumber as user services; ALSA (`pipewire-alsa`, `default` device), PulseAudio (`pipewire-pulse` and `libpulse` for the apps that load it; the PulseAudio daemon masked). SOF firmware (`package/sof-bin`) and UCM profiles (`package/alsa-ucm-conf`) for the Intel DSP and digital microphones. `osyr audio status/volume/mute [--mic]/test`, media keys and `VOL` in the bar. No JACK |
| Voice | Press and hold Super+Space (or `/voice` in the chat): `pw-record` 16 kHz → whisper.cpp (`package/whisper-cpp`, in `/opt/whisper` with its own ggml 0.26 and CPU variants; llama.cpp's is 0.9.x) with the small q5_1 model (~190 MB, `make model`) → the text reaches the chat (SIGUSR1). Spoken answer with espeak in the detected language; `/speak on` for all of them. `● REC` in the bar while recording |
| Screen: screenshots and `look` | Print Screen → `osyr-screenshot` (`grim`, own package) in `~/Pictures/Screenshots`; `osyr-diag` uploads the last 3 (`--no-screenshots` to skip). `/look [question]`, `osyr ai look` and the agent's `look` tool: screenshot scaled down to 1024 px and a question to the local model with its vision projector |
| Agent v0 in the chat | Tools `osyr` (the CLI, JSON) and `look`. Queries run by themselves; anything that changes something asks for confirmation (and the contract token when the command requires it). Turns trimmed whole. If the provider does not support tools, it carries on without them |
| Chat history | `~/.local/share/osyr/chats/*.json`; `/history` and `/resume [n]` |
| First boot | `osyr-firstboot` before the chat: own password (via `chpasswd`, never on the command line) and root locked; wifi if there is no network. Passwords are typed with one `*` per character (backspace and Ctrl+U erase; not built or tested in QEMU yet: `make test` checks it in `firstboot-password`). Marker in `~/.local/state/osyr/firstboot-done` |
| Signed updates | `osyr update code` on the `release` channel by default: only `v*` tags signed with SSH (key in `/etc/osyr/update-signers`; the private one on the VPS, `~/.ssh/osyr-release`), never downgrades. `CHANNEL=main` for development, unsigned. `scripts/release.sh vX.Y.Z` signs a release |
| `make test` | `tests/run.py`: native phase (image binaries on the VPS: agent with tools, `look`, voice, external provider) and vm (QEMU snapshot: `tests/vm-checks.sh` over the serial console with virtual wifi, sound recorded by QEMU, screenshots, diag, update, first boot) and install (`osyr-install` to a second disk and booting that disk). Results in `build/test-results/` |
| Screens | `osyr ui display list/on/off/mode/scale/extend/mirror`: swaymsg; each choice is saved per monitor (make, model, serial) in `~/.config/sway/config.d/50-osyr-displays.conf`; never turns off the last screen; mirroring with `wl-mirror` (own package) |
| Lock and idle | `swaylock` + `swayidle` (own packages): lock at 5 min, screen off at 10, lock before suspending (lid). Super+Ctrl+L. Theme in `/etc/swaylock/config` |
| Notifications | `mako` (own package), theme in `/etc/xdg/mako/config`; `osyr/notify.py` over standard D-Bus. Low battery at 10 % and 5 %: notification and spoken warning, once per threshold |
| Bluetooth | BlueZ (client, audio, HID, HOG) + Intel, Realtek, MediaTek and Qualcomm firmware. `osyr bt status/on/off/scan/connect/disconnect/remove` over D-Bus; pairing in Just Works mode (keyboards with PIN, pending). Headphones show up by themselves in PipeWire |
| Full first boot | Language (`/etc/locale.conf`), keyboard (sway immediately + console), time zone, password and network. Generated locales: es, en, ca, fr, de, it, pt |
| Hardware self-test | `osyr sys selftest` (and inside `osyr-diag`): CPU, GPU, screens, brightness, battery with wear, wifi, Bluetooth, sound (1 kHz tone through the speaker, looked for in what the microphone records, Goertzel), model and voice (espeak → whisper, timed) |
| Boot on the HP 15s | **Done** (29 Oct, USB, UEFI): self-test 10/10 without failures (iris, 2 screens, wifi, Bluetooth, battery, model, voice); two `osyr-diag` in `~/osyr-diag/` on the VPS. What came up and its fix, in "First boot on the HP" |
| Disk installer | `sudo osyr-install` from the USB (details in "Disk installer"). Built and tested in QEMU (`make test` 103/103, 30 Oct). HP 15-ba (BIOS): installed and **boots fine from the disk** (31 Oct) |

## v0.30.0 (OSYR, 4 Nov)

- `make test` on the first OSYR image: vm 135/135, native 16/18. The two failures were `memory-saved`/`memory-recalled`: asked "Recuerda que mi hermana se llama Ana", the 4B called `clipboard` (set) instead of `memory add` (in v0.28 it called `memory add` without text). **Fix**: a dedicated `remember` tool in `osyr-chat` with a required `text` (it runs `memory add`); `memory list|forget` stay in the `osyr` tool; the clipboard description says it is not for remembering. Native 18/18 after the fix.

## Image `a6ebbb8` published (31 Oct, afternoon)

- `make test` vm 122/122 with the `a6ebbb8` image (`DIRTY=no`). Published `osyr.img.xz` (sha256 `e5bdade088bf2196c057902884b4a02a05e01985ad7921db3d57d50214f31a9a`) and `osyr.ova` (sha256 `68bf9c59133d294e01d5b072ce296c0f6d8771005377da0bd385cfbd4cac1137`; its VMDK boots in QEMU in BIOS and UEFI). The `d6fd3fa` ones stay in `build/prev-d6fd3fa-*`.
- Carries everything from 31 Oct: browser, `osyr files`/`doc`, vision on demand, memory, backups, USB, `osyr-diskwatch`, installer with optional encryption, lock at 20 min, AI usage notice, `osyr-diag` privacy, help and `osyr-hwtest`.
- **v0.26.0** signed on `a6ebbb8` (the image commit) with its changelog entry; website published with v0.26.0; push of `main` and `v0.26.0` done.
- `publish.sh img vX.Y.Z` now makes the release: it signs the tag on the image commit (`release.sh` accepts the commit as a third argument), refuses if `web/changelog.html` lacks its entry or if the image is already another version, and pushes `main` and the tag before publishing (if that fails, it does not publish; it requires being on `main` with the image commit in it). `vbox` requires the image to already have a version. The website takes the version from the commit of the published image, not from HEAD.
- **Next**: the user boots it on the HP 15s and the HP 15-ba and runs `osyr-hwtest`; review their diags.

## Handover (29 Oct, night) — read first

Night session: first boots on the HP 15s (2019) and on the HP 15-ba (old), fixes and AI providers. Details in the sections "First boot on the HP 15s", "Old laptop" and "Firewall and AI providers".

- **Image published** (30 Oct, 08:01): commit `dbd89d6` (`DIRTY=no`), `make test` 92/92. Carries all the improvements from the night of the 29th to the 30th and, at 3 % battery without a charger, an on-screen and spoken warning and suspend after one minute (cancelled when plugged in). `https://ccmai.org/osyr/releases/osyr.img.xz`, sha256 `6d39e335d12eac0afea65a6e2124dcf63b2f78297ce2a703a2286cb441056097`. The previous ones stay in `build/osyr-d89c73c.img.xz` and `build/osyr-17h55.img.xz`.
- **Pending confirmation on the HP 15s with the new image**: beep and microphone (if they fail, the diag now brings `audio-codec.txt` and `audio-alsa.txt`), speed between messages (stable template), wifi from the agent (password without echo), screen lock, `/pair` with the ccmai.org server (first test with a real key). On the HP 15-ba: that sway starts (waits for udev in `osyr-session`).
- **VPS**: the receiver (`osyr-diag-receiver`) also serves the pairing (`/osyr-diag/pair`); restart it with `sudo systemctl restart osyr-diag-receiver` after touching `diag-server.py` or `pair.html`. The Docker llama-server with Caddy (`llama-qwen`, `llama-proxy`, `webuillama.ccmai.org:8443`) belongs to the **llama-hardened** project: do not touch it or use its keys.
- **Next: disk installer** (decided with the user): wipes the whole disk, will be tested on the HP 15-ba (it has an old OSYR version that can be lost). Proposed design:
  - `osyr-install` (or `osyr sys install`) from the USB: picks the disk (never the USB's own), confirmation by typing the disk name, summary of what gets wiped.
  - GPT with a BIOS boot partition (GRUB i386-pc) + FAT ESP (GRUB EFI, kernel, `grub.cfg`), for BIOS and UEFI like the USB.
  - Root A and root B (ext4, same size as the USB's or larger): A is copied; B stays empty for the A/B update (done in v0.27 with GRUB, without RAUC). `grub.cfg` with the default slot in a GRUB environment variable, so A/B fits in later.
  - `/home` on LUKS2 (cryptsetup), unlocked at boot with systemd-cryptsetup (asks for the passphrase on tty1 before the session). The root is not encrypted: there is no initramfs (lesson from newos).
  - Packages to review: `cryptsetup`, `BR2_PACKAGE_SYSTEMD_CRYPTSETUP` (or its equivalent in 2026.08), `dosfstools`, `e2fsprogs`, `gptfdisk`/`sfdisk`, kernel with `DM_CRYPT` and AES/XTS `=y`.
  - Test first in QEMU with an extra virtual disk (BIOS and UEFI), add it to `make test`, and only then on the laptop.
- **Push** only when the user asks (last: 30 Oct, `c516b26`).

## Disk installer (30 Oct)

`osyr-install` (Python, in the overlay). It runs from the USB and **wipes the whole disk**. Interactive: lists the disks (never the USB's, nor read-only ones or those under 24 GB), summary of what is there and what gets wiped, confirmation by typing the disk name, and the /home passphrase twice (min. 8). Without questions: `--disk /dev/sdX --confirm sdX --passphrase-file F`; `--list [--json]`. On an already installed OSYR (`/etc/osyr/install.conf`) it refuses.

| Partition | Contents |
|---|---|
| sector 0 and gap up to 1 MiB | `boot.img` (440 bytes) and GRUB BIOS core, like the USB |
| 1, ESP FAT32 256 MiB | `EFI/BOOT/BOOTX64.EFI`, `osyr/grub/grub.cfg`, `osyr/grub/grubenv` (`slot=a`) |
| 2, root A ext4 | copy of the running system (bind of `/`, `cp -a`), without `/home` |
| 3, root B ext4 | empty, for A/B (milestone 4) |
| 4, LUKS2 → ext4 | `/home` copied from the USB; `crypttab` with `tries=0` (root is locked: emergency mode would be of no use) |

Roots of 1/8 of the disk, between 8 and 16 GiB. Everything is copied as is: password, language, wifi, providers and history stay the same.

Decisions:
- **MBR and not GPT** (a change from the handover design): it is the USB's scheme, which already boots in BIOS and UEFI on both laptops, and GRUB BIOS goes in the gap without patching sectors. Exactly four primaries; if more are needed (or disks > 2 TiB), move to GPT with a BIOS boot partition.
- The kernel does not go in the ESP: GRUB reads it from `/boot/bzImage` on the active root (`search --fs-uuid`), so an A/B update changes system and kernel at the same time. `root=PARTUUID=<signature>-02/03`.
- The disk has its own GRUB cores (`post-build.sh` → `/usr/lib/osyr/boot`, with `loadenv`), which look for `/osyr/grub/grub.cfg`: with the USB and the disk both plugged in, each one boots its own (the USB's looks for `/boot/grub/grub.cfg`).
- New packages: `cryptsetup` (systemd builds `systemd-cryptsetup` with it; forces a build from scratch) and `dosfstools` (`mkfs.vfat`). The kernel already had `DM_CRYPT` and XTS `=y`.
- The agent does not run it: it points to `sudo osyr-install` in a terminal.
- UEFI without an NVRAM entry (there is no `efibootmgr`): the fallback path `EFI/BOOT/BOOTX64.EFI` is used, which almost every firmware boots. The HP 15-ba is BIOS.

Tested before building, on the VPS: installation onto a loop disk with the root of the `dbd89d6` image (without LUKS at boot, that image has no `systemd-cryptsetup`) and boot in QEMU in BIOS, in UEFI and with `slot=b`. Built from scratch with cryptsetup (30 Oct) and `make test` **103/103** (native 9, vm 83, install 11), not committed yet. Found by the test: the image does not ship `wipefs` (a util-linux option not enabled); it is not needed, `sfdisk --wipe always --wipe-partitions always` erases the old signatures (checked by reinstalling over an installation with LUKS). Full test in `make test-install` (the `install` phase of `make test`): installs from the USB in QEMU to a second disk, boots it in BIOS (wrong and right passphrase over the serial console) and in UEFI with the USB also connected.

## Night of 29 to 30 Oct: improvements on the `mejoras-termica-bateria-updates` branch

Done while the user slept, at their request, **outside the image published** that night (the `d89c73c` one). Each one with piece-by-piece tests and new checks in `make test`; the image with everything is built and tested after publishing the other one.

| Improvement | What it does |
|---|---|
| Temperature (6) | `osyr sys thermal` (coretemp/k10temp/zones, Intel throttling counters, frequency); self-test; bar `CPU 71°C` / `HOT` / `THROTTLED`, warning at 95 °C; agent query |
| Battery profile (7) | `osyr-power.service` + udev rule: on battery, EPP `balance_power`, brightness ≤ 60 % (goes back when plugged in if it was not touched) and the model with half the threads (restarts llama-server only if it is idle; otherwise it retries after a minute). `/etc/osyr/power.conf`, `osyr sys power` |
| Update notice (8) | `osyr update code notify` + daily `osyr-update-check.timer`: notifies once per version, never installs; `AUTO_CHECK=no` |
| Reminders (4) | `osyr remind add/list/cancel`, user timers with absolute time, on-screen and spoken notice, `restore` on entering sway (reports the missed ones); the agent creates them without confirmation |
| Clipboard (5) and dictation (3) | Own packages `wl-clipboard` 2.3.0 and `wtype` 0.5. Agent `clipboard` tool (with a remote provider it asks before sending); dictation with Super+Ctrl+Space (`osyr-voice dictate`) |
| Swap in RAM | `osyr-zram.service`: zram of half the RAM (≤ 8 GiB), swappiness 150 |
| Fixed prompt processed in advance | `osyr-chat` processes system + tools in the background when it opens, while the user types: the first message processes ~15 tokens instead of ~900 (on the HP 15s, ~50 s less waiting). Saving it to disk so it survives reboots (restore: 10 ms) **does not work with the vision projector loaded**: llama.cpp b8117 answers 501 "not supported by multimodal", and in OSYR it always is. It stays attempted (`--slot-save-path`); it will work if a new llama.cpp version supports it or if vision is split off |
| Battery and memory | Remaining time in the bar; suspend at 3 % without a charger; `OOMScoreAdjust=500` on the model |
| Diag and tests | Diag with swap/zram, temperature, power and reminders. QEMU: end-to-end pairing and agent wifi with password without echo; native: llama-server as `osyr-llama` and the prompt cache |

**Status (30 Oct, 02:00)**: branch merged into `main` (fast-forward, no push). Image of `e1ca6cd` built and `make test` **92/92** (native + QEMU). **Not published**: the published one is still the `d89c73c` one. Found and fixed along the way: `osyr-zram.service` created a boot cycle with `tmp.mount` (systemd dropped the `/tmp` mount and then bluetooth.service mounted it on top of whatever was there); new test `boot-no-cycles`. The VM checks now run from a file with input from `/dev/null`.

Pending hardware test: all of the above. Decided with the user (30 Oct): suspend at 3 % with prior warning; dictation stays on Super+Ctrl+Space.

## Previous handover (29 Oct, end of the long session)

To pick up in a new session: read this section, the status table and `git log`.

- **Done and tested** (table above). Tests: `make test` (native phase: image binaries on the VPS; vm phase: QEMU). Last run before the final build: native 8/8, vm 40/43, and the 3 failures (`chpasswd` was missing from BusyBox; root checks) fixed in the next commit.
- **29 Oct, afternoon**: final build done (53 min). `make test`: native 8/8, vm 53/53 after fixing `osyr-firstboot` (`sudo -k -S`: with the password in the sudo cache, the password line reached `chpasswd` and the change failed if a language or time zone had been chosen first), `osyr bt` (without `/sys/class/bluetooth` it is `BT_NO_ADAPTER`, not a service down) and three tests (swayidle turns off the VM screen before the screenshot; the image does not ship `locale`; Bluetooth without an adapter).
- **Image for the HP published** (29 Oct, 17:55): `/var/www/ccmai.org/osyr/releases/osyr.img.xz` (+ `.sha256`), built from the commit with these fixes. The user tests it before going on with the improvements.
- **Unpublished**: (historical: superseded by the push of 30 Oct). Do not push or regenerate/publish the `.xz` unless the user asks. `osyr update code` on the HP will see nothing until there is a push and a signed release (`scripts/release.sh vX.Y.Z`, key `~/.ssh/osyr-release` on the VPS).
- **VPS**: `osyr-diag` receiver active (`osyr-diag-receiver`, Apache `/osyr-diag/` on ccmai.org; bundles in `~/osyr-diag/`). The VPS also runs another llama-server with Qwen3.6-35B (Docker, `llama-qwen`, behind Caddy on :8443 with `X-API-Key`): do not use its key without permission.
- **Next step of milestone 0**: boot on the HP 15s, run `osyr-diag` (uploads self-test, screenshots and logs) and review here what arrives.
- **Decision with the user**: the "Proposed improvements" below (reviewed 4 Nov: most are done; the rest are in "Still open").

## First boot on the HP 15s (29 Oct, night)

HP Laptop 15s-fq1xxx, i5-1035G1 (Ice Lake, AVX-512), 7.5 GiB, ALC236 over classic HDA (the internal microphone is pin 0x12 of the codec, SOF is not needed). Model: 13.8-24 tok/s prompt and 3.5-4.1 tok/s generation with 4 threads.

| Finding | Cause | Fix (pending test on the HP) |
|---|---|---|
| No microphone (`WARN sound`; PipeWire without any source, nor input profile) | Buildroot runs `systemctl preset-all` and was also enabling the **system** services of PipeWire, pipewire-pulse and WirePlumber (user `pipewire`, without HOME): a second WirePlumber opened the card before osyr's | `disable` in `10-osyr.preset` |
| Every new message took 1-2 min before answering: llama-server reprocessed 1,600-2,200 tokens | The Qwen3.5 template only renders `<think></think>` in the answers after the last question; when another one arrives, the history changes and in a hybrid model the cache goes back to an old checkpoint or to zero | `osyr-llama` generates at startup a template with the block in every answer (`gguf.py template`). Measured on the VPS with the image binaries: from ~590 tokens reprocessed per message to ~21 |
| agetty on ttyS0 failing and restarting every 10 s (360 error lines in the journal) | `console=ttyS0` (for QEMU and tests) makes systemd put a getty on a serial port the laptop does not have | `ExecCondition` in `serial-getty@.service.d/osyr-uart.conf`: only if `/sys/class/tty/ttyS0/type` is not 0 |
| Bluetooth without audio (WirePlumber: "BlueZ SPA plugin is missing") | `pipewire.mk` only enables bluez5 with `BR2_PACKAGE_SBC` | `BR2_PACKAGE_SBC=y` |
| The agent could not connect the wifi: it asked for confirmation and then the password did not work; besides, it was visible while typing | The password went in the command the model writes (`--password <p>`): the user typed it in the chat in plain sight (and it stayed in the saved conversation) and the 4B model had to copy it, and the network name, character by character | `osyr-chat` intercepts `net wifi connect`: finds the network with a scan, asks for the password without echo (asking for it is already the confirmation; Enter cancels; up to 3 attempts) and the model never sees it. `net.py` accepts an approximate name if only one network matches ("livebox 6s f406") |
| The model only uses 4 of the 8 threads | `THREADS=auto` = physical cores | `-tb` (prompt) with all threads but one (`THREADS_BATCH` in `llama.conf`); generation stays at 4, because generation is limited by memory. The `osyr-diag` benchmark now uses a ~260-token prompt to also measure prompt reading |
| Clock 1 h ahead until NTP syncs (after 13 min, with the wifi) | The laptop has Windows: the RTC is in local time | Untouched: it is the usual dual boot issue. If it bothers, `timedatectl set-local-rtc 1` or set Windows to UTC |

Follow-up (same night): on the HP 15s **the beep could not be heard either** (the self-test said "output OK" anyway). In the diag PipeWire marked the headphones as connected and the speaker as unavailable; the kernel applies the generic HP fixup (`ALC269_FIXUP_HP_MUTE_LED`, the 86C9 does not have its own). Not confirmed whether it is due to the double PipeWire. `osyr-diag` now collects `audio-codec.txt` (pins and jack detection), `audio-alsa.txt` (`amixer contents`, audio processes with their uid, PipeWire system units).

Screen lock (swaylock) impossible to dismiss: it rejected every password. PAM checks the password with `unix_chkpwd`, which must be setuid root to read `/etc/shadow`, and in the image it was 0755 (post-build cannot set setuid). Fix: `board/osyr/permissions.txt` in `BR2_ROOTFS_DEVICE_TABLE` (4755). New test in QEMU: good password accepted and bad one rejected by `unix_chkpwd`, as swaylock does. In the meantime: Ctrl+Alt+F2 and `pkill swaylock`.

## Handover (30 Oct, afternoon) — read first

- **Disk installer done** (section "Disk installer"): commit `d6fd3fa` (pushed). `make test` 103/103 before the commit; with the `d6fd3fa` image (`DIRTY=no`) `make test-install` 11/11. Image ready in `build/osyr-d6fd3fa.img.xz`, sha256 `6a86b71c53164c3982fa95d77cd780d2aa7e2ca7be4a361ecd6fc5a7e133a4b8`; **published** (30 Oct, afternoon) at `https://ccmai.org/osyr/releases/osyr.img.xz`; the previous one (`dbd89d6`) stays in `build/osyr-dbd89d6.img.xz`. Publishing requires the user's explicit permission in the session (Claude Code's permission control stops it otherwise).
- The user tests it on both laptops (warning given: on the HP 15s it wipes Windows).
- More robust test: the login over the serial console waits up to 5 min (one run failed with 120 s: it coincides with the tty1 session starting without KVM).
- **Next**: image for the HP 15-ba with the installer and the midday fixes; there, `sudo osyr-install` onto the internal disk (BIOS), boot without the USB and with it plugged in.
- Hardware test: that the firmware boots the disk (BIOS: `boot.img` in the MBR; UEFI: fallback path), the `/home` passphrase on tty1 with the es keyboard, and the copy time.

## Full review with Fable (2 Nov) — published as v0.29.2 (image c7aa25c, .img.xz and .ova)

Four reviewers (Fable 5.1, read only) over the whole repo; every serious finding checked in the code before touching it. Fixed in four commits:

- **Security:** `osyr web` only opens `http(s)` (with `file://` the model read `~/.config/osyr/ai.json` without asking); `osyr-hwtest` no longer kills `swaylock` after 5 min (it unlocked without a password); `osyr` out of the `input` group; `diag.token` 640 `root:osyr` (`permissions.txt`, always exists, empty = no upload); the website and the README no longer promise "never your conversations" (`/report chats` sends them).
- **Updates:** live-reload restarts on anything it does not know (except `usr/bin`, `usr/lib/osyr`, `usr/share`, `etc/osyr`, `etc/swaylock`, `etc/xdg/foot`, `etc/sudoers.d`, `etc/pam.d`); enabled oneshots (`osyr-power`) are really relaunched; the chat applies what is pending by applied id (`$XDG_RUNTIME_DIR/osyr/session-applied`), also after updating from a terminal; `/update undo` towards a chat without `--continue` does not lose the window. A/B: `image confirm` requires sway running and `osyr-llama` not failing (up to 5 min, `TimeoutStartSec=10min`); `image rollback` asks for a token and keeps `code-commit`/backups of the slot it goes back to. `publish.sh` generates and verifies the manifest signature before publishing anything; order update → img → manifest. `post-build`: `DIRTY` counts untracked files and deletes `__pycache__`.
- **Installer and first boot:** `osyr-install` checks the space before wiping and stops if the disk is still in use after releasing it; Ctrl+C says whether the disk had already been wiped. `osyr-firstboot` writes the marker even if a step fails or takes long, and with `--force` asks for the current password.
- **Functionality:** reminders with `ExitType=cgroup` (the voice was cut off); suspend at 3 % with `sudo -n systemctl suspend` (`sudoers.d/osyr-power`: without polkit logind denies it, checked on systemd 258); the bar checks the remote provider every 5 min; `osyr-hwtest` reads the slot from `slots.running()`.
- **Tests:** web over HTTP on 127.0.0.1 and `file://` rejected; `no-input-group`, `suspend-allowed`, `diag-token-private`; image rollback with token.
- **Test on the HP:** suspend with a drained battery (or `sudo -n systemctl suspend` as osyr), a reminder that plays in full, sway and the keyboard without the `input` group.

**Pending from the review (not blocking):** all done on 3 Nov (following sections).

## Secrets off the command line (3 Nov) — for the next code release

Before, the wifi password went in `osyr net wifi connect --password` (the chat and first boot) and in `iwctl --passphrase`; a provider's key, in `osyr ai add --api-key`; the `osyr-diag` token, in the curl arguments. All of that shows up in `ps` and in `/proc/<pid>/cmdline` while it runs.

- **Wifi:** `--password-stdin` (the chat and `osyr-firstboot` send it over stdin) and, towards iwd, `net.iwctl_asking()`: **interactive** iwctl in a pseudo-terminal, it types `station <dev> connect "<ssid>"` and answers its "Passphrase:" prompt. Tested with iwd 3.12 in QEMU: iwctl only asks in interactive mode (with the command as arguments and without `--passphrase` it answers "Operation aborted") and only accepts the password **letter by letter** (all at once, "Operation aborted" even if it is right). It ends on seeing an iwctl error or the station connected, and exits with `quit`. The password is checked first (8 to 63 characters, or 64 hexadecimal).
- `--ask`: asks for the password even if the network is saved (the router's changed); the chat understands it and asks without echo. If the saved one still works, iwd connects with it and does not ask. `--password` still works (same path through iwctl), with a warning in the help. The hints no longer suggest `--password`.
- **`osyr ai add`:** `--api-key-stdin`; with no key and no terminal, the hint suggests typing it in a terminal or `osyr ai pair` (which already saved it without going through the command line).
- **`osyr-diag`:** the token goes in a headers file (`curl -H @file`, `mktemp`, deleted afterwards).
- Tested in QEMU with the v0.29.2 image and the new `osyr` package (`mac80211_hwsim` access point from `make test`): wrong → "Operation failed" with no saved profile; short → rejected; right → connects with an IP; saved network, `--ask` and `--password` → connect; a watcher over `/proc/*/cmdline` the whole time never saw the password. vm-checks: `wifi-*` over stdin, new `wifi-short-password` and `wifi-password-not-in-argv`.

## The small items from the review (3 Nov) — for the next code release

No new image except where marked. Tested on the host what can be (Python functions, manifest signing, shell loops); the rest, with `make test` before publishing.

- **Processes:** `voice.alive(pidfile, name)` only treats the pid as alive if `/proc/<pid>/cmdline` is that program (a dead chat that did not delete its pidfile left a pid the kernel can give to another process). The bar writes `osyr-status.pid` and is repainted through it; no more `pkill -f osyr-status`.
- **`slots.part()` without udev:** if there is no `/dev/disk/by-partuuid` (emergency mode, where `image fallback` runs), it looks for the MBR signature at byte 440 of each disk in `/sys/block`.
- `osyr disk list` does not crash if a USB was unplugged without ejecting (`disk_usage` → `?`). Automount label without ".", ".." or hidden names (with ".." the mount point ended up outside `/run/media/osyr`).
- `osyr-diag`: tar and gzip in two steps (with the pipe a tar failure went unseen), screenshots with spaces in the name, an empty `SEND_CHATS` in the user's file does not override the system one. `osyr-zram stop` never leaves the unit failed at shutdown.
- **Image:** kernel fragment without `MODULE_SIG=n` (lockdown selects it, it cannot be removed; commented); post-build also sets 0755 on `usr/lib/osyr/osyr-*`; wl-mirror selects wayland-protocols; sherpa-onnx explains why there are no `LICENSE_FILES` (the binary does not ship the texts). `download-model.sh` checks the sha256 of Qwen, mmproj and whisper (the ones from Hugging Face's `x-linked-etag`; the local ones match).
- **`osyr-update-check.service`** hardened: `ProtectSystem=strict` (writes only `/var/lib/osyr` and `/run/osyr-update`), `ProtectHome=read-only` (checks free space in `/home/.osyr-update`), no devices or kernel, minimal capabilities (switches to `osyr` to notify). vm-check `update-check-service` really starts the unit.
- **VPS:** `diag-server.py` purges `hits` and `last_by_ip`, deletes `.part` at startup and if the upload fails. Receiver restarted (3 Nov, with the rename).
- **Publishing:** `publish.sh vbox` refuses if the published `osyr.img.xz` is not the image commit; the website installs `web/assets` (the five logos on the front page, previously only on the server). The downloads page links the signed manifest, the `.sig`, `update-signers` and the `ssh-keygen -Y verify` command to check it.
- **Signature tests:** vm `image-bad-signature` (manifest signed by another key → "not signed by an OSYR release key"); install `update-image-tampered` (manifest altered after signing it with the good key → `image check` fails).
- **Docs:** README (v0.29.2, Piper, image A/B), ARCHITECTURE (own A/B without RAUC, whisper.cpp + Piper in the base, three fixed tools, USB image and .ova instead of ISO), this file (gate passed, "B empty").

## Code updates without rebooting (1 Nov) — v0.29.1 (code)

- The user: "can we avoid the reboots?". Image updates no (another root and another kernel: only by booting it; there is no hurry, it comes in at the next power-on). Code updates almost always: before, `apply`/`rollback` always flagged a reboot.
- **`osyr/live.py`**: `plan(files)` decides; `apply_system` (root) runs `daemon-reload`, `try-restart` of the affected services (llama and vision through `llama.conf`/`osyr-llama`; diskwatch through its script or the library, which imports `notify`; firewall through nftables; power; any changed unit) and restarts `systemd-resolved` if its configuration changes; it leaves in `/run/osyr/session-reload` what belongs to the session (`sway`: config, theme, `osyr-status` bar or the library; `idle`; `mako`; `chat`: the chat or the library). Only what is read at boot flags a reboot: `fstab`, `crypttab`, `modprobe.d`, getty, emergency, zram, presets, logind/journald/system.conf.
- **The chat** (`/update`, `/update undo`): if there is a reboot flag, it asks as before; if not, it applies the session items (`swaymsg reload` relaunches the bar; `osyr-idle` again; `makoctl reload`) and, if the chat changed, saves the conversation and relaunches itself in the same window with `--continue <conversation>` (without the intro; "the conversation goes on (N earlier questions)").
- The first time (v0.29.0 → v0.29.1) it still asks to reboot: the v0.29.0 updater decides that.
- Tests: on the host, `plan` with cases (real v0.28.1 → chat + DNS, no reboot; fstab → reboot) and the chat against `mock_openai` with a fake `sudo` that leaves the session flag (it relaunches, 1 conversation, "1 earlier questions"). New: native `chat-update-relaunches`, vm `update-no-reboot` (a new command: "Nothing to restart", no reboot flag).

## Natural voice: Piper with sherpa-onnx (1 Nov) — for v0.29

- The user: espeak sounds "like the 90s". Now **Piper** (VITS voices) with **sherpa-onnx 1.13.8**: `sherpa-onnx` package with the official binary of the release (like sof-bin; sha256 pinned), only `sherpa-onnx-offline-tts` and `libonnxruntime.so` in `/opt/sherpa-onnx` (~35 MB, RPATH `$ORIGIN/../lib`). Building it would download an already built onnxruntime in the middle of the build anyway. The 2.x series removed the built-in phonemizer (espeak-ng, GPL): with 2.x the phonemes would have to be passed in.
- **Voices** (`make model` → `models/piper/<language>/{model.onnx,tokens.txt,sid,MODEL_CARD}` and a single `espeak-ng-data`, ~430 MB; `post-build.sh` copies them to `/usr/share/osyr/models/piper`; since they are under `models/`, the small update does not download them again): es `sharvard` voice 2 and en `libritts_r` voice 0 (chosen by the user with samples on a private page); ca `upc_ona`, fr `siwis`, de `thorsten`, it `riccardo` (only x_low exists), pt `faber` (only pt_BR): the seven first-boot languages. **Licences**: the non-commercial ones are out (lessac and its fine-tunes amy and paola, ryan, hfc); the CC BY ones credited in the help ("Credits").
- **Speed** (VPS, 1 thread): the medium ones generate ~11× faster than real time; loading the model ~0.7 s. `voice.speak()` splits the text into sentences and `osyr-say-piper` generates the next one while the previous one plays: it starts speaking in ~1 s (2014: ~2–3 s). The "high" one (daniela) was 7× slower: discarded. Without a voice for the language, espeak as before. Process group, waiting for notices and silence while recording, unchanged.
- **First run of `make test` (154/159), three real failures**: (1) `sherpa-onnx` was not in the image: the `Makefile` did not reapply the defconfig when it changed (the `.config` rule only depended on the Buildroot Makefile); now it also depends on `configs/osyr_x86_64_defconfig`, and checked that every `=y` in the defconfig is in `.config`. (2) `image-manifest.py --update` failed with `espeak-ng-data` (hundreds of small files; `debugfs stat` gave no size for some): now only files of 1 MiB or more are removed from the small image, with the size from `ls -p`. (3) A v0.28 does not have the voices and its updater is "all or nothing": it would download the whole image (4.1 GB). `--keep PREFIX` (`OSYR_UPDATE_KEEP` in `publish.sh`) puts those files whole into the small image: for v0.29.0, `usr/share/osyr/models/piper` → 768 MB instead of 4.1 GB. From v0.30, without `--keep`. Also `memory-saved`/`memory-recalled`: the 4B called `memory add` twice without the text; it happened in v0.28 too: fixed in v0.30.0 with the `remember` tool (see "v0.30.0").
- **Second run: 170/170** (also `memory-*`: it was variability). **v0.29.0 published** (image `c56807c`): img 4.15 GB, `osyr-update.img.xz` 774 MB with `OSYR_UPDATE_KEEP=usr/share/osyr/models/piper` (MODELS without the voices), ova.
- The downloaded binary could not be run on the VPS (the permission control blocks it: external code); the speed was measured with the Python module of the same engine and the binary is tested inside the image: vm `voice-piper-es`/`voice-piper-en` (WAV of 1–10 s) and `voice-one-at-a-time` no longer depends on espeak. On the host, the pipeline with a fake synthesiser and player: order, overlap, cut-off and cleanup.

## No-warranty notice (1 Nov)

- Requested by the user: the usual notice that the software comes without warranty. Text (from the MIT licence, in plain words): "OSYR is provided "as is", without warranty of any kind: you use it at your own risk, and its authors are not liable for any damage or loss of data (MIT License)." On first boot, the chat (short version), the help (with the suggestion of `osyr backup run`), the website (about, downloads) and "MIT License · no warranty" in the footer of every page.
- The website, published. The system: on `main`, for the next code release.
- The user confirms that the 2014 one has v0.28.1: the image apply left the new slot with the latest signed code (`sync_code`), which is why `/update` said up to date.

## v0.28.0 and v0.28.1 published (1 Nov)

- **v0.28.0** (image `487191f`, `make test` 168/168): img 3.74 GB, `osyr-update.img.xz` 368 MB (models with the same sha256 as v0.27), signed manifest and ova (boots in BIOS and UEFI).
- **v0.28.1** (code, `dbf4d30`): chat log, `SEND_CHATS`/`/report chats` and `DNSSEC=no`. Simulated from v0.28.0 on a fake root: with local `main`, check/apply (5 files identical to HEAD)/rollback; with `release` against GitHub, `v0.28.1 (signed)`.
- **2014 (full v0.27, checked by the user: "system B image cce6839953", wifi on its own)**: `sudo osyr update code apply` (v0.28.1, brings the new updater) and then `/update` → small image. With `/update` directly, the v0.27 updater would download the whole one.

## More debugging: chat log, conversations in the report and DNS (1 Nov) — v0.28.1 (code)

- Reason: a `/report` from the 2014 one (v0.27) did not say what happened with `/look`: the chat left no trace. The user asked to be able to send the conversations and for more debugging.
- **`~/.local/state/osyr/chat.log`** (JSON lines, rotates at 512 KB into `.old`; `osyr-diag` already collects `*.log`): `start`/`switch` (provider, model, server, context; never the key), `reply` (seconds, tokens, prompt tokens, tools requested, empty answer), `error` (HTTP code and 300 characters of the reason, or the exception), `tool` (tool, first two words of the command, code and error), `look` (code, error or length of the answer, seconds), `unreachable`. Never the text of the conversation. Tested on the host against `mock_openai`.
- **`SEND_CHATS`** (off by default) in `/etc/osyr/diag.conf`, overridable by each user in `~/.config/osyr/diag.conf`; `osyr-diag --chats`/`--no-chats` for a single time; `/report chats` in the chat. The last 5 conversations of the last 7 days are sent (up to 2 MB) and it always says so. Help and assistant prompt updated. Tested on the host: default, user setting, `--chats`, and `--no-chats` wins over the setting.
- **`DNSSEC=no`** in `resolved.conf.d/10-osyr.conf`: in the report, ~1 min of "DNSSEC validation failed for ollama.com: no-signature" (16:58:40–16:59:32) while resolved was downgrading its mode with the router's DNS (fe80::1); during that time ollama.com did not resolve. Like Ubuntu and Fedora.

## Only one voice at a time and /look with remote models (1 Nov) — for v0.28

- **Overlapping voices** (the user, on the 2014 one): `speak()` only killed the `sh` of `espeak | pw-play` and the voice went on; and chat, reminders and battery did not know about each other. Now each voice is a process group recorded in `$XDG_RUNTIME_DIR/osyr/speaking`; a normal voice (chat answer) cuts all the groups (`killpg`); `wait=True` (reminders, battery) waits inside its own sh, up to 30 s, for the ones playing to finish. The outer sh leaves the voice in the background and exits: that way no zombies are left (with them `kill -0` treated the group as alive and the notices never played, seen in the test on the host). The text goes as an argument and through `printf`, never through the shell. Recording (Super+Space, `/voice`) silences the voice first.
- vm test `voice-one-at-a-time`; on the host with a fake espeak: cut-off, waiting in order for two notices and no stray processes.
- **/look with Ollama Cloud** (the user: deepseek v4.1 flash, "it doesn't seem to work"; without the exact message): with a remote provider `max_tokens` 2048 (the reasoning ones spent the 512 before answering); empty answer → clear message (`EmptyAnswer`, tells "only thought" apart from "does not see images"); the chat shows the reason for the server's HTTP error, not just the code. Pending: the user tries it and says what comes out.

## Problem description in `osyr-diag` (1 Nov) — for v0.28

- Requested by the user: whoever sends a diagnostic can tell what happened to them. With a terminal (`/report` in the chat, or `osyr-diag` by hand) it first asks "what happened?", over several lines until an empty one (Enter alone = skip), and warns not to type passwords. It goes into `description.txt`, the first thing to read in a diagnostic. `--message TEXT` to give it without asking; `--quick` (`osyr-hwtest`) does not ask.
- Tested on the host with a pty; vm `diag-description` with `--message`.

## Image update without downloading the models (1 Nov) — for v0.28

- The models are 3.3 GB of the 3.7 GB of `osyr.img.xz` and do not change between versions. `publish.sh img` also publishes **`osyr-update.img.xz`** (~370 MB): the root alone, with the data of `usr/share/osyr/models` zeroed (the files remain, with their size). `scripts/image-manifest.py --update` makes it without mounting anything: `debugfs dump_extents -l` gives the data blocks of each model (only the leaves, never the extent tree), their sha256 is computed by reading them and they are zeroed in the extracted partition; `e2fsck -fn` and the sha256 of each model against `build/output/target` check it.
- The signed manifest adds `UPDATE_IMAGE`, `UPDATE_SHA256`, `UPDATE_SIZE`, `UPDATE_ROOT_SHA256` and `MODELS=path:sha256:size,…`; the earlier fields remain (v0.27 downloads the whole image as before).
- The laptop (`plan()`): if every model in its root has the manifest's sha256, it downloads the small one, writes it and copies its models to the new slot (`copy_models`, checking the sha256 while copying); if any does not match (updated model), the whole image. `check` only looks at sizes (it is quick) to say how much it will download.
- Test: the first apply of `update_image_test` uses the small one (`update-image-small`, `update-image-models`: sha256 of the models in B); the second, with a manifest without the small one, the whole one (`update-image-full`).

## Image update: A/B slots (1 Nov) — v0.27.0

- **`osyr update image check|apply|rollback|status|confirm|fallback`** (`osyr/image.py`, `osyr/slots.py`): downloads `osyr.img.xz` (curl with `-C -` to `/home/.osyr-update`), verifies the signed manifest (`osyr.img.xz.manifest` + `.sig`, `ssh-keygen -Y` in the `osyr-image` namespace, same signers as the code) and the sha256 of the xz, writes the root into the free slot (vda2 ↔ vda3) and checks its sha256; then `e2fsck`, new UUID and label, and `resize2fs`. Only later versions (git ancestry in the `update.fetch()` clone).
- **What moves to the new slot** (`migrate`): accounts and passwords (merge of passwd/shadow/group), locale, keyboard, hostname, machine-id, time zone, crypttab, install.conf, the machine's SSH keys, wifi (iwd), Bluetooth, systemd time and brightness, linger; `/etc/osyr` with a three-way merge (what the person changed stays; if it clashes with the new one, the new one goes to `.osyr-new`); the `/home` line of fstab. Then `update code apply` against the new root (`OSYR_UPDATE_ROOT`) so that it ends up on the announced commit.
- **Boot**: `grub.cfg` with `grubenv` (`slot`, `try`). The new slot boots once on trial (`try`, GRUB clears it with `save_env`); if it does not reach `multi-user` (`osyr-boot-ok.service` confirms it) or enters emergency mode (`osyr-fallback.conf`), the next boot goes back to the previous one. "OSYR (previous version)" entry in the menu; `panic=10` so that a broken kernel reboots by itself.
- Chat: `/update` uses `osyr update apply` (image if there is a new version and the system is installed; otherwise, code) and `/update undo` (code first, then the image). The daily notice checks the image first.
- Publishing: `scripts/image-manifest.py`, and `publish.sh img` publishes the manifest next to the image (also `publish.sh manifest`). Defconfig: `resize2fs`.
- **From an installed v0.26**: `/update` (code v0.27.0, brings the new updater) → reboot → `/update` again (v0.27 image in slot B, with the state). v0.26 already has `xzcat`, `ssh-keygen`, `tune2fs`, `e2fsck` and GRUB's `loadenv`; it does not have `resize2fs` (the new root fits as is; it grows in the next image).
- New test `update_image_test` in `tests/run.py install` (unencrypted disk): apply → boots B with the changed password, a wifi network and `CTX=8192` → `slot=b`, empty `try=` → rollback to vda2 → a B without `bzImage` goes back to vda2 by itself. First run: the test itself wrote its configuration wrong (`… | echo osyr2 | sudo tee` → tee received the password); fixed with `sudo sh -c`.
- **Bug found by the test**: `rollback` from B gave `INTERNAL_ERROR`: the git clone (`/var/lib/osyr/src`, on the root) did not move to the new slot and the `/etc/osyr` merge needs it. Now `migrate` copies it (8 MB); if it is missing, it is downloaded, and without a network the other slot keeps its `/etc/osyr`. A git `RuntimeError` no longer escapes in apply or rollback.
- Two other failures were in the test itself: `echo osyr | sudo -S chpasswd` gave chpasswd the sudo password (it never changed) and everything went through the sudo cache until it expired during the apply; now `sudo sh -c 'echo osyr:osyr2 | chpasswd'` and `sudo -k`. The console of each reboot is saved in `update-image-rebootN.txt`.
- Others in this image: HTTPS in the browser, first-boot wifi (`a9b05e9`), ccache at 20G.
- **v0.27.0 published** (1 Nov): signed tag on `cce6839`, img + signed manifest + ova (boots in BIOS and UEFI). `make test` 162/163: the only failure, `vision-on-demand`, is a timing one without KVM (the server finished loading at 300 s; `vision-server-up`, right after, passes; it passed in the previous run). Pending: test on the 2014 one the path v0.26 → `/update` (code) → reboot → `/update` (image).

## v0.26.1: first code update published (1 Nov)

- Signed tag `v0.26.1` on `770d444`, pushed to GitHub. No image: 10 overlay files (chat, ai, pair, net, update, diag, firstboot, hwtest, sys, help); the browser HTTPS (defconfig) waits for the next image and `check` warns about it.
- Simulated on the VPS as a v0.26.0 laptop (`v0.26.0` overlay on a fake root, `OSYR_UPDATE_ROOT`/`OSYR_UPDATE_STATE`, against GitHub): `check` sees `v0.26.1 (signed)` and the 10 files; `apply` leaves them identical to HEAD; with the new code `check` says up to date and `rollback` brings back v0.26.0.
- On the 2014 one (v0.26.0 installed today): `sudo osyr update code apply` and reboot; from there on, `/update`.

## Less terminal for those who cannot use one (1 Nov)

- **`/update`** and **`/update undo`** in the chat: `sudo osyr update code apply|rollback` in the same window (shows the changes and asks). If the installed version changed (`/var/lib/osyr/code-commit`), it offers to restart the computer (`Restart now?`). The daily notice says "Type /update in the OSYR chat"; if `llama.conf` or `osyr-llama` changes, `apply` restarts `osyr-llama` itself; the hints say "Restart the computer" instead of "log out and back in". The agent no longer has `update code check` (it needs root: it always gave `NEEDS_ROOT`); it suggests `/update`.
- **`/report`**: `osyr-diag` in the same window; the agent suggests it when it cannot solve a problem (it does not launch it: it takes data off the machine).
- `/pair` from the agent: the hint no longer sends you to the terminal. After `/pair` or `osyr ai use` it no longer says "Restart the chat": the chat follows the active provider before each message (also if it was changed from another terminal).
- Help: "Useful commands" says that almost everything can be asked of the assistant; `/update`, `/report` in the chat table.
- The clipboard no longer brings the chat down without `wl-paste` (happened in the native test: the model chose the clipboard instead of memory; a one-off, two more runs 17/17).
- **Pairing**: the "ccmai.org server" provider removed from the page (the user's decision: they set up their server by hand). In "Other" there is an optional context field (the `-c`), which reaches the laptop. `osyr-diag-receiver` restarted on the VPS (3 Nov).
- **Usage notice**: it now says that OSYR is in development and can make mistakes, and so can its AI (chat, first boot, help and website).
- Wifi not showing up at first boot: the user saw it again on the 2014 one with v0.26; the fix (`a9b05e9`) is later than v0.26, it goes in the next image.
- `tests/run.py native` 17/17 (new: `chat-install-runs-installer`, `chat-update-runs-update`, `chat-report-runs-diag`).

## HTTPS in the browser (1 Nov)

- **Bug** (the user, on hardware, 31 Oct): cog did not open https pages ("TLS support is not available"). `libsoup3` without `BR2_PACKAGE_LIBSOUP3_SSL`: without `glib-networking` there is no GIO TLS module (`/usr/lib/gio/modules` did not even exist). Only http and file opened.
- **Why the tests did not catch it**: `web-*` uses `file://` pages, and `hwtest-web-open` opens `https://ccmai.org/osyr/` but gave OK with any text, and the error page also has text. Now it requires the text to say "OSYR" (with `OSYR_HWTEST_URL`, any text is enough).
- **Fix**: `BR2_PACKAGE_LIBSOUP3_SSL=y` → glib-networking + gnutls + p11-kit (trust in `/etc/ssl/certs/ca-certificates.crt`). Checked in `.config` after applying the defconfig. **It enables gnutls and p11-kit, which change packages already built** (ffmpeg moves to `--enable-gnutls`, systemd to `-Dp11kit=enabled`): the next image, with `make clean all`. Built and tested: `hwtest-web-open` passes against https://ccmai.org/osyr/ in v0.30.0 (test-vm 135/135).

## /install in the chat (1 Nov)

- Many people will not know how to use a terminal: `/install` in the chat launches `sudo osyr-install` in the same window (like `/pair`); password, disk, confirmation and encryption are answered there. If `/etc/osyr/install.conf` exists (already installed) it says so and does not launch it. The agent never runs it: the prompt tells it to suggest `/install` (wiping a disk is the person's decision, and a 4B can misread "install the browser"). Help (`/help`, `help.md`) updated.
- Native test `chat-install-runs-installer` (with a fake `sudo`); `tests/run.py native` 15/15.

## Provider context behind a proxy (1 Nov)

- **Bug**: with a llama-server behind a proxy that only exposes `/v1` (the ccmai.org one: Caddy does not let `/props` through), `detect_context` took `meta.n_ctx_train` from `/v1/models` (262144 in Qwen3.6-35B) as the real context; the server runs with `-c 32768` and long conversations would have failed. `/pair` left it like that with no way to correct it.
- **Fix**: the training context (`ai.TRAINING`) is no longer taken as valid: `osyr ai add` asks for it (or `AI_NEEDS_CONTEXT` with `model_max` if not interactive) and `osyr ai use --model` does not apply it. The pairing page sends `context` if known (optional field in "Other") and `diag-server.py` lets it through (integer 1024–10M); `pair.py` uses it instead of detecting it.
- Backwards compatible: v0.26 ignores the pairing `context`. For an already installed v0.26: `sed -i 's/262144/32768/' ~/.config/osyr/ai.json`.
- Tested on the host: new mock `train-ctx` (without `--context` → `AI_NEEDS_CONTEXT`, interactive asks, with `--context` it is fine; `ctx-in-models` still detects), against the real server (`AI_NEEDS_CONTEXT` without `--context`, 32768 with it) and end-to-end pairing with a local `diag-server.py` (`context=4096` arrives). vm `ai-pair-flow` now checks the received context. `make test` passed with the v0.30.0 image and `osyr-diag-receiver` was restarted (3 Nov), so the published page carries the context.

## Agent browser, files and searchable help (31 Oct)

- **Browser** (improvement 13, decided with the user: WPE WebKit + cog, no Firefox; no DRM, so no Netflix/Spotify): `BR2_PACKAGE_WPEWEBKIT` (sandbox, multimedia with GStreamer and gst1-libav, WebDriver) and `COG` (Wayland); sound through `pulsesink` to pipewire-pulse. `osyr web open|read|go|click|fill|back|status|close` speaks WebDriver with `WPEWebDriver` (127.0.0.1:4445), which launches `cog --automation`; the page is read as text (2,200 characters) and a numbered list of links, buttons and fields. Navigating and reading do not ask; `click` and `fill` do; `fill` refuses passwords and cards (`WEB_SENSITIVE_FIELD`). Super+O (`osyr-web-prompt`, with wmenu) opens an address or searches (DuckDuckGo); cog windows go to workspace 2 in tabs. No address bar on purpose. Built (the binary is called `WPEWebDriver`, confirmed). **Pending check with the image**: the `wpe:browserOptions` capability, cog's `app_id`, that cookies persist in automation mode and that WebKit draws with llvmpipe in QEMU. Tests in `vm-checks.sh` (`web-*`) with a local page.
- **`osyr files find|list|read`** (improvement 1, and 7: summarise or translate a file): read only; home folder, mounted disks, `/tmp`; never `~/.config/osyr`, `~/.ssh`, `~/.gnupg`...; reads in chunks of 3,000 characters; PDF with `pdftotext` (poppler, added to the defconfig, goes into the next build). With a remote provider, the chat asks before sending the content.
- **`osyr doc <words>`** (improvement 3): sections of `help.md` by similarity; the agent prompt tells it to use it before explaining how to do something.
- Tool output the model sees: from 2,000 to 4,000 characters (~1,000 tokens, as ARCHITECTURE says). Fixed agent prompt: ~960 tokens.

## Vision on demand, memory and backups (31 Oct)

- **Vision only when used** (improvement 10, and with it 9): `VISION=ondemand` in `llama.conf` (default). The agent server starts without `--mmproj`, so it uses ~700 MB less and llama.cpp does save the fixed prompt to disk (with the projector it gave 501). `/look` asks `127.0.0.1:8084`: `osyr-llama-vision-proxy.socket` → `systemd-socket-proxyd --exit-idle-time=10min` → `osyr-llama-vision.service` (`osyr-llama vision`, 8085, ctx 8K, `StopWhenUnneeded`; it only counts as active when `/health` answers). The model is mmapped: both processes share its pages. `look.py` checks `/props` (`modalities.vision`) and, if the agent's server already has vision (`VISION=always`), asks it as before. The automatic context still counts the projector, so that both fit. Tests: native with two servers (`native-vision-server`, `look-sees-screen`, `prefix-saved-to-disk`); vm `llama-no-mmproj`, `vision-socket`, `vision-on-demand`, `vision-server-up`.
- **User memory** (improvement 4): `osyr memory list|add|forget <n|all>`, in `~/.local/share/osyr/memory.json` (0600); max. 30 notes of 200 characters. It refuses to save passwords, PINs, keys and card numbers (`MEMORY_SENSITIVE`). The chat adds the notes to the system prompt **on every request and only with the local model**: they are not sent to a remote provider; what is saved applies from the next message. `add` and `list` do not ask; `forget` does. Native test `memory-saved` / `memory-recalled` (two conversations).
- **Backups** (improvement 15): `osyr backup run|restore <disk>`, `osyr backup status`. rsync (added to the defconfig) in the background to `<disk>/OSYR-backup/<user>`, notifies when done; `-rt --modify-window=2` on FAT/exFAT/NTFS, `-a` on the rest. Without `.cache`, the trash or the folders with keys (the `osyr files` ones, `--delete-excluded`); warns if `/home` is encrypted (the backup is not). `restore` without deleting and with `--update`. The agent: `status` without asking, `run`/`restore` ask. vm test `backup-run` on the virtual pendrive.

## Extended guided test: browser, vision and USB on the laptop (31 Oct)

- `osyr-hwtest` (about 10 min) adds what QEMU cannot confirm. **Browser** (`web`): opens `https://ccmai.org/osyr/` and measures the time; `web-window` checks in sway cog's `app_id`, the workspace (2) and whether it is in tabs; `web-cookie` sets a cookie, closes cog, opens it again and checks whether it is still there (if not, logged-in sessions do not last: warning; in QEMU they are lost, because cog in automation mode does not save cookies to disk); `web-codecs` (`canPlayType` of H.264, VP9, AV1, AAC, Opus and MediaSource); `web-video` and `web-audio` with a WebM VP8 video and a WAV tone generated by the image's ffmpeg, served by a local HTTP server (with `file://` the video gave "not supported": in QEMU, `err 4` and `NotSupportedError`), the sound with a real WebDriver click; it asks whether it is seen and heard. **Vision** (`vision`): two `osyr ai look` (cold, it starts the vision server; warm, it is already up), memory of the vision server and free memory, that the agent model keeps answering, and asks whether the description is right. **USB** (`usb`): `osyr-diskwatch` active, asks to plug in a pendrive, waits for the mount (30 s), writes and deletes a file, asks about the notice and ejects it.
- In QEMU, `vm-checks.sh` runs `osyr-hwtest --auto --only web,usb` (`hwtest-web-open`, `-video`, `-audio`, `-diskwatch`); window, cookies and codecs are only noted (`INFO` in `vm-checks.txt`).

## USB and external disks, disk error notices (31 Oct)

- **Automount** (improvement 16): rule `90-osyr-automount.rules` (USB and SD) → `osyr-automount@<partition>.service` (root) → `/run/media/osyr/<label>`, owned by the user (FAT/exFAT/NTFS with `uid`), `nosuid,nodev,noexec`; on-screen notice. Never the system disk (the one with `/` or `/home`, also behind LUKS); LUKS ones are not opened (notice). `osyr disk list|eject <name>`: ejecting uses `sudo -n` with a rule only for `osyr-automount eject` (`/etc/sudoers.d/osyr-disk`; post-build sets 0440 on all of `sudoers.d`). Tested in QEMU with a virtual FAT pendrive (`qemu-xhci` + `usb-storage`).
- **`osyr-diskwatch.service`** (improvement 22): follows the kernel log and reports disk/USB errors, at most once every 10 min per device.

## Installer and screen lock (31 Oct)

- `osyr-install`: **optional encryption** (asks "Encrypt them? [Y/n]"; `--no-encrypt` without questions; `ENCRYPTED=yes|no` in `install.conf`, no `crypttab` and `/home` by UUID if not encrypted). The passphrase is typed with asterisks (`osyr.term.read_password`, shared with `osyr-firstboot`). During the copy it creates `/run/osyr/no-idle` and `osyr-idle` neither locks nor turns off the screen (suspected cause of the black screen on the HP 15-ba at the end). New test in `make test-install`: a second `--no-encrypt` installation over the encrypted one, boots without a passphrase.
- Idle lock: **20 min** by default (before, 5), screen off 5 min later. `osyr ui lock [minutes|off]` (the agent too) saves `~/.config/osyr/idle.conf` and relaunches swayidle through `osyr-idle`. Tests in `vm-checks.sh`.

## AI usage notice (31 Oct)

The AI can make mistakes: check before confirming; no sensitive data or sensitive operations (banking, payments, important files); with a remote provider, what you type goes to that provider. On first boot (once, with Enter; the `firstboot-password` test expects it), at every chat start (dim line), in the help (and `help.html`) and on the website (front page, about, downloads; published). Not built into an image yet.

## Diags of 31 Oct (night) and wifi at first boot

- Two diags arrived (21:37 and 21:45), **both from VirtualBox** (i5-1035G1, 4 threads, BIOS): 9/11, no failed services. Model ~11 tok/s reading and ~2.2 tok/s generation; voice 13 s. Neither has `hwtest.txt` (`osyr-hwtest` was not run). **No diag has arrived from the HP 15-ba.**
- Sound FAIL in VirtualBox: the microphone records nothing (in VirtualBox audio input is disabled by default; not an OSYR issue).
- **Fixed**: Bluetooth gave FAIL ("service not running") without an adapter; without `/sys/class/bluetooth` systemd does not even start the service. Now WARN "no Bluetooth adapter", as `osyr bt` already did.
- **Fixed**: the diag did not say which image it came from. Now it brings `build-info.txt` (`/etc/osyr/build-info` and the `code-commit` applied with `osyr update code`).
- **Wifi at first boot on the HP 15-ba** (reported by the user): `osyr-firstboot` found no networks and `osyr net` later did. `net.scan()` waited 0.5 s and considered the scan finished if `Scanning` was not true, and at most 10 s. On that machine the station appears late, iwd is busy with its autoconnect scan (`Scan` answers Busy) and the full scan is slow. Now it waits until there is a station and no scan in progress, requests the scan, waits for it to start (3 s) and to finish (30 s) and, if it comes out empty, repeats it once. Tested with a simulated iwd (station after 2 s, Busy until 5 s, 12 s scan): before it returned empty and now it finds the network. Pending: confirm it on the HP 15-ba with the next image.

## Guided hardware test: `osyr-hwtest` (31 Oct)

To check on the laptops everything pending without going item by item. Unattended: boot (BIOS/UEFI, USB or disk, slot, encrypted `/home`), boot time and model load time, failed or restarting services, disk/USB errors in the kernel, zram, firewall, update timer, full self-test, model with two messages in a row (speed, and that the second does not reread everything: < 100 tokens), temperature and throttling while it works, clipboard, 1-min reminder, Bluetooth scan, internet, pairing server and `osyr update code check`. With a person (y/n, Enter skips): disk passphrase at boot, whether the beep and the reminder were heard, unplugging and plugging in the charger (battery profile), locking and unlocking the screen, dictating "hello computer" into the terminal itself. It saves `~/.local/state/osyr/hwtest/last.{txt,json}` and offers `osyr-diag --no-screenshots --quick` (`--quick`: without repeating the self-test or the model measurement; the diag always includes the last result). `--auto` without questions, `--only`, `--list`. Tests: native `hwtest-model` against the image's llama-server; vm `hwtest-auto` and `diag-quick-hwtest`. Not built into an image yet.

## Website visibility (31 Oct)
Following `~/ccmai-hub/VISIBILITY.md`. `render-web.py` (`social_meta`) adds to each page `canonical`, Open Graph (with `og:site_name`) and `twitter:card`, taken from its `<title>` and its `description`; the image is `web/og.png` (1200×630, PNG because social networks do not show SVG; generated by `scripts/make-og.py` from the real screenshot in `tests/data`) and `publish.sh web` installs it. The footer of every page links to ccmai.org, kai, miuzika and falken. GitHub: homepage, description and topics set (`gh` installed on the VPS; it uses the token from `~/.git-credentials` with `GH_TOKEN`). Published; checked with the Twitterbot and LinkedInBot user agents (tags on all 6 pages, `og.png` served as `image/png`).

## Publishing and website (30 Oct, night)

- **v0.25.0** signed and pushed (the numbering continues from the previous system's, the user's decision). Since `d6fd3fa` nothing in the system changes: machines with that image see it as up to date.
- `scripts/publish.sh img|vbox|web [preview]`: `img` publishes `osyr.img.xz`; **the user is notified** and then `vbox` publishes `osyr.ova` (VBoxManage converts; its VMDK is booted in QEMU in BIOS and UEFI before publishing, the VPS has no VT-x). Both regenerate the website at the end. Both from `d6fd3fa` published.
- The website lives in `web/` as templates (`@VERSION@`, `@IMG_SHA@`...) that `scripts/render-web.py` fills in with what was actually published. Front page with the two downloads, what OSYR does and requirements; `releases/` with sha256 and instructions (USB, boot, `osyr-install`); new changelog (v0.25.0). Live at ccmai.org/osyr since then (v0.30.0 on 4 Nov).
- From the HP 15-ba (2014) with the `d6fd3fa` image: the installation went fine, but at the end the screen stayed black and it had to be powered off with the button. Suspicion: swayidle locks and turns off the screen during the copy (long on that machine) and radeon does not come back. The diag did not arrive (no upload since 10:58). Requested by the user: **optional encryption** in the installer.
- **`osyr-diag` privacy** (on `main`, for the next image): it never carried conversations, keys or wifi passwords (also checked in the real journal: llama-server only logs metadata). Now it asks before including the last 3 screenshots (default no; without a terminal, no; `--screenshots` / `--no-screenshots`) and of the reminders it only sends how many there are. New tests in `vm-checks.sh`. The website says what the report contains.
- **Help** (on `main`): `/usr/share/osyr/help.md` is the single source; `osyr-help` shows it in the terminal (Super+F1, in `less`), the chat's `/help` and the agent refer to it, and `render-web.py` generates `https://ccmai.org/osyr/help.html` (published). Locked or black screen, shortcuts, chat and CLI commands. Added Super+Shift+arrows (the help listed them and they did not exist).
- **Update notices checked** with the published image (`d6fd3fa`) in QEMU: against GitHub it sees `v0.25.0 (signed)` and is up to date; against a local copy with a `v0.25.1` signed with the real key (never pushed) it notifies once ("OSYR update available", 5 files, no new image), not the second time, and the timer is active. The `make test` tests only covered the unsigned channel.
- README in English (the user's request), pushed.
- **For the next image**: installer (optional encryption; no locking or screen-off during the copy, to be confirmed with a diag from the HP 15-ba) + `osyr-diag` privacy + help (Super+F1).

## Handover (30 Oct, midday) — read first

- **Image published**: the `dbd89d6` one (see "Handover (29 Oct, night)" below). What comes after it on `main` goes in the next image, not built yet.
- **HP 15s (2019)**: the user uses it for work; no testing on it for now. Hardware tests are done on the **HP 15-ba**.
- **Diag from the HP 15-ba with the `dbd89d6` image** (`~/osyr-diag/20261030-105817-osyr-0d6745.tar.gz`): self-test 11/11. sway starts with radeonsi (the udev wait fix confirmed), sound and microphone fine, wifi fine. Findings:
  - Model: ~3 tok/s reading and ~1.1 tok/s generation (`ivybridge` variant, no AVX2). **Hardware limit documented** in `ARCHITECTURE.md` ("Target hardware"): on this machine, remote provider. No small model is added (the user's decision).
  - A `/look` (596 tokens) in the single slot wiped the chat prompt already processed: the next message reprocessed 985 tokens (5.5 min). **Fixed**: `osyr-llama` with `-np 2 --kv-unified` (each slot can use the whole context, same memory; only the recurrent state, ~50 MB, is duplicated); the chat goes to slot 0 (`SLOT` in `osyr-chat`), `/look` and the diag benchmark to slot 1. Native test `prefix-warm` with an intermediate request to slot 1; checked that it fails (956 tokens) if it goes to slot 0.
  - `systemd-networkd-wait-online` failed: `osyr-update-check.service` pulled it in, and it waits for every interface (also the Ethernet without a cable). **Fixed**: the service no longer depends on `network-online.target`; without a network, `osyr update code notify` gives `UPDATE_CHECK_SKIPPED` (does not fail) and tries again the next day.
  - Battery: `BAT1 (battery absent)`, dead or removed: the battery functions cannot be tested on this machine.
  - The USB disappeared for an instant after 2 min (`Medium not present`, 4 reads with errors) and recovered: contact or pendrive. Keep an eye on it.
  - The model takes 2.5 min to load from the USB.
- **The agent made up how to install OSYR to disk**: the installer does not exist yet. Fixed in the `osyr-chat` system prompt: if something is not among its tools' commands, it says so instead of inventing steps; and the installer "is not available yet". When it exists, update that sentence and add the command to `TOOLS`.
- **Next**: the disk installer (design in "Handover (29 Oct, night)"), tested in QEMU and then on the HP 15-ba. Next image: installer + the fixes above.
- **Push**: done (30 Oct, `c516b26`). There is no `~/.gitconfig`: the credential store has to be given, `git -c credential.helper=store push origin main`; and the identity, `git -c user.name="Carlos Carrillo" -c user.email=ccarrillomanzanares@gmail.com commit ...`.

## Old laptop: HP 15-ba (29 Oct, night)

HP Notebook, board 82F6, AMD A8-7410 (Mullins, Radeon R5, `radeon`), 6.7 GiB, BIOS (no UEFI), RTL8723BE wifi. Sound with microphone fine; voice: 56 s for 1.3 s of speech (the model was still loading).

| Finding | Cause | Fix (pending test) |
|---|---|---|
| sway does not start: "Could not take device: No such device", "Found 0 GPUs" | Race: autologin launches sway at 10.8 s and radeon finishes at 12.8 s; udev still had a busy queue (it loaded `amdgpu` at 27 s, slow CPU and USB) and `/dev/dri/card0` was still `root:root 0600`, with no seat. The GPU probe could not open the render node either and forced pixman | `osyr-session` waits for `udevadm settle` (up to 45 s, with an on-screen notice) and, if sway exits with "Found 0 GPUs", waits again and retries from the start with the GPU probe |

## Firewall and AI providers (29 Oct, night)

- **Firewall** (`osyr-firewall.service`, `/etc/nftables.conf`): own table `inet osyr`; everything goes out, only established/related, loopback, ICMP/ICMPv6 and DHCP come in. Starts before the network. Local rules in `/etc/nftables.d/*.nft` (e.g. SSH). Tests: `drop` policy loaded and reload without duplicating.
- **Choosing a model**: `osyr ai models [provider]` and `osyr ai use <provider> --model <id>` (recomputes the context). In the chat: `/ai`, `/ai <name> [model]`, and the chat switches provider only if the active one changes (also if the agent changes it).
- **Pairing** (`osyr ai pair`, `/pair` in the chat): the laptop generates an RSA-3072 key in `XDG_RUNTIME_DIR`, asks the VPS for a code and shows a URL and QR code (`qrencode`). At `https://ccmai.org/osyr-diag/pair` (phone or PC) you pick the provider and paste the key; the browser encrypts it with WebCrypto (RSA-OAEP SHA-256) and the VPS only keeps ciphertext, in memory, for 10 min, and deletes it on delivery. The laptop decrypts with `openssl`, shows what it received, asks for a name and model and confirms before using it. Server: `scripts/diag-server.py` + `scripts/pair.html` (per-IP limits with the real IP, the last one in `X-Forwarded-For`). Tested end to end against the real server with a simulated provider.
- Providers on the page: the ccmai.org server (llama-hardened, `https://webuillama.ccmai.org:8443/v1`, `X-API-Key`; it is another project: OSYR only uses it, does not touch it), OpenAI, Anthropic, Gemini, Ollama Cloud (`https://ollama.com/v1`; its `/v1/models` does not give the context: `detect_context` gets it from `/api/show`, which also works for a local Ollama), OpenRouter and any compatible one. Only the path and the format are tested; with real keys, none yet.
- **Cloudflare returned 403 to `Python-urllib`**: the VPS model and the pairing would not have worked from OSYR. Every request now carries `User-Agent: OSYR/1.0` (`ai.headers()`). The test mock server rejects that agent like Cloudflare.
- Disk installer: in the next image, wiping the whole disk (it will be tested on the HP 15-ba, which has an old OSYR version).

## Decisions already taken

- Buildroot **2026.08.1**, glibc toolchain, **x86-64 baseline** CPU (runs on any 64-bit PC).
- Buildroot's own llama.cpp (**b8117**) with `GGML_BACKEND_DL` + `GGML_CPU_ALL_VARIANTS`; `post-build.sh` moves the variants to `/usr/lib/ggml`. Checked in the b8117 code: it already has the `qwen35` architecture and the `qwen3vl_merger` projector, no need to bump the version.
- Milestone 0 model: `unsloth/Qwen3.5-4B-GGUF`, **Q4_K_M quantisation (2.74 GB)** + vision projector (`mmproj`). It is kept in `models/` (outside git) and `post-build.sh` copies it to `/usr/share/osyr/models/`. 7 GB ext4 root so that it fits.
- Graphical session: sway (reads i3 configuration) + foot; wlroots with a software renderer if there is no 3D GPU. libseat uses logind (systemd), no seatd daemon.
- 16K local context; port 8083 as in the previous system.
- **mesa with LLVM** (29 Oct): iris and crocus (Intel), radeonsi (AMD) and llvmpipe require it in mesa 26; iris also needs the precompiled compiler (host-mesa3d + libclc). Buildroot silently drops iris if LLVM is missing. Before (28 Oct) it went without LLVM and on Intel and modern AMD sway drew with pixman.
- **Each image for real hardware bundles all the verified changes**: the build → download → write USB → test cycle is long. Verify on the VPS everything possible beforehand (Kconfig, build, QEMU, scripts run inside the image).

## First install on the HP 15-ba (4 Nov, v0.30.0)

- Diag `20261104-205124`: AMD A8-7410, 6.7 GiB, UEFI, `/home` on its own partition (not encrypted), battery absent (`BAT1`). Self-test 11/11, no failed units; sway starts and wifi connects (both pending on this machine).
- Slow CPU: local model 1.2 tok/s generating, 3.1 tok/s reading the prompt (first reply 189 s, the next 15 s); dictation 32 s for 1.3 s of speech. A remote provider makes sense here.
- **Bug** (reported by the user): `/pair` right after opening the chat, then "hello": the answer came only after the local model finished loading. The chat waited for the local warm-up (fixed prompt, minutes on this CPU) even with a remote provider. Fix: `wait_warm` only waits when the active provider is local. Native test `chat-switch-during-warmup` (`tests/slow_llama.py`, 60 s warm-up): fails before, passes after.
- **Emoji shown as boxes** (reported by the user): the image only had DejaVu (Sans Mono has no emoji). New package `noto-emoji` (Noto Color Emoji, CBDT, 10.7 MB, OFL; FreeType already links libpng) and `/etc/fonts/local.conf`, which puts it right after DejaVu and the generic families, before DejaVu Sans's black-and-white emoji. Checked with the target's fontconfig on the host; vm-check `emoji-font`. Pending: see it with the next image (terminal, bar, notifications, browser).

## Proposed improvements (reviewed 4 Nov)

Done: firewall (`nftables`), dictation into any window (`wtype`), reminders, clipboard tool, temperature in the bar and self-test, battery profile, update timer, file and help tools, browser (WPE + cog), disk installer with encrypted `/home` and own A/B (no RAUC), prompt cache kept across messages.

Still open:

1. Camera for vision (`/look camera`).
2. Image and PDF viewer, file manager.
3. Hands-free wake word ("Hey OSYR"), optional: CPU cost to be measured.
4. Model speed: speculative decoding with a small draft model. Measure on the HP.

## Still pending (4 Nov)

- **Hardware tests with v0.30.0** (`osyr-hwtest` covers most of them): on the HP 15s, beep and microphone, speed between messages, wifi from the agent, screen lock, `/pair`; on the HP 15-ba, sway start and wifi at first boot; on the 2014 one, `/update` from v0.26 (code, then image).
- **Browser in the image**: `wpe:browserOptions`, cog's `app_id`, cookies in automation mode, llvmpipe in QEMU.
- **/look with Ollama Cloud**: the user tries it and reports the message.

## Lessons from this milestone

- Changing `configs/osyr_x86_64_defconfig` was not enough: `make` did not reapply it and the new package (sherpa-onnx) did not reach the image. Fixed in the `Makefile`; even so, after touching the defconfig, check `.config`.

- Enabling a package that gives new options to others already built (ICU → HarfBuzz) does not rebuild them: WPE failed to configure without `libharfbuzz-icu` (31 Oct). After enabling large packages, look at which already built packages have `ifeq ($(BR2_PACKAGE_X),y)` for the new ones and run `<package>-dirclean` on those that need it.

- Kconfig silently drops options whose dependencies are missing (it happened with `MESA3D_GALLIUM_DRIVER_IRIS` without LLVM). After touching the defconfig, check that every `BR2_...=y` is still in `build/output/.config`.
- With `BR2_PER_PACKAGE_DIRECTORIES`, changing the configuration of a base package (systemd, PAM) requires a build from scratch (`make clean all`); with ccache it is ~30 min.
- In Buildroot 2026.08's `users.txt`, uid `-1` = system and `-2` = normal user. Better to pin it.
- **wlroots does accept llvmpipe** in virtual machines: with `virtio_gpu` without virgl, mesa falls back to `kms_swrast` on the hardware device, wlroots does not detect it as software and sway draws with 4 llvmpipe threads. That is why mesa is asked for the driver before starting sway, instead of trusting wlroots. Check the real renderer through the threads (`/proc/$(pgrep -x sway)/task/*/comm`).
- The image userland is partly BusyBox (`tar` without `-z`): a script that works on the VPS can fail on OSYR. Test it inside the image (QEMU serial console on a socket).
- QEMU on the VPS runs without KVM: the model speed there (~0.4 tok/s) says nothing; measure on real hardware.
- The disk filled up mid-build (3 Nov: kernel with debug info, 25 GB, plus the old tree). Now `make` runs `scripts/disk-guard.sh` first: it deletes build dirs of packages no longer in the configuration or with an old version (Buildroot never does), images and .ova of versions that are neither published nor current, all but the latest `build/prev-*` and the 3 latest `build/web-prev-*`, and stops if less than 30 GB are free (100 GB without `build/output`; `OSYR_MIN_FREE_GB`). `make prune` only frees. A full `build/output` is ~80 GB.

## Lessons inherited from newos (do not repeat)

- No initramfs: disk and USB controllers must be `=y` in the kernel or the root does not appear (black screen).
- GRUB on UEFI reads `/EFI/BOOT/grub.cfg`: Buildroot's points to the FAT partition and gives a black screen.
- Scripts edited on Windows: normalise CRLF and run `chmod +x` **afterwards**.
- Buildroot's generic `grub.cfg` in the root confuses GRUB's search: it is deleted.

## Build (on the VPS)

```bash
git clone https://github.com/ccarrillomanzanares/osyr && cd osyr
make model   # Qwen3.5-4B Q4_K_M + mmproj into models/ (~3.4 GB)
make         # clones Buildroot 2026.08.1 into build/buildroot the first time
make qemu    # or 'make qemu-uefi'; QEMU_DISPLAY=vnc=:1 on a headless server
```

The image ends up in `build/output/images/osyr.img`.
