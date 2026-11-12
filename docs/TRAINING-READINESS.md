# Training readiness — inventory

13 November 2026 · **Partial.** Stage 0 of [TRAINING-PLAN.md](TRAINING-PLAN.md). This is the part the repository can answer by itself, at v0.31.1. It becomes the frozen description of the interface when stage 0 ends.

## Verdict today

OSYR is **not ready** to train on: the agent handles settings, files (read only), memory and the browser, and the goal is a Linux user and administrator. What exists is consistent and verifiable, which is a good base; what is missing is listed at the end.

## What the model sees

| Piece | State |
| --- | --- |
| Tools | Four: `osyr(command)`, `clipboard(action, text)`, `remember(text)`, `look(question)` |
| Fixed prompt | About 960 tokens (system text plus the four tool descriptions). Limit in the architecture: 1,500 |
| Commands inside `osyr` | The list is written in the tool description; anything else is refused before running (`USAGE`) and goes back to the model with a hint |
| Confirmation | Every command that is not in the read-only list asks `Run 'osyr …'? [y/N]`. Sensitive ones ask a second time with what the command explains (`CONFIRM_REQUIRED`) |
| Tool output | JSON, trimmed to 4,000 characters |
| Sampling | The server's defaults: `osyr-chat` sends no temperature or seed. The benchmark needs a way to fix them |

## Commands the CLI has and the agent is not told about

The tool description is shorter than the CLI. If these should be usable by the agent, they have to be in the description before the freeze; if not, the benchmark must not expect them.

| CLI command | In the agent's list |
| --- | --- |
| `bt on`, `bt off`, `bt disconnect`, `bt remove` | No (only `status`, `scan`, `connect`) |
| `audio volume --mic`, `audio mute --mic` | No |
| `sys power apply --profile` | No (only reading `sys power`) |
| `ai add`, `ai remove` | No, on purpose: adding a provider goes through `/pair` |
| `update …` | No, on purpose: the user types `/update` |

## Does every change have a command to read it back?

Yes. The verifier of a task, and the agent itself, can check each change:

| Change | Read back with |
| --- | --- |
| `audio volume`, `audio mute` | `audio status` |
| `audio voice speaker`, `audio voice listen` | `audio voice status` |
| `ui brightness <n>`, `ui lock <n>` | The same command without a value |
| `ui display extend`, `mirror` | `ui display list` |
| `net wifi connect`, `disconnect` | `net status` |
| `bt connect` | `bt status` |
| `ai use` | `ai status` |
| `remind add`, `cancel` | `remind list` |
| `disk eject` | `disk list` |
| `backup run`, `restore` | `backup status` |
| `remember`, `memory forget` | `memory list` |
| `clipboard set` | `clipboard get` |
| `web click`, `web fill` | `web read`, `web status` (the page itself is outside the machine) |

## Can it run without real hardware?

This decides how many instances can run in parallel in containers.

| Area | Without hardware today | What it talks to |
| --- | --- | --- |
| Brightness, temperature, battery and power | **Yes**: `OSYR_SYSFS` points to a fake `/sys` | sysfs files |
| Files, memory, help | **Yes**: a temporary home folder is enough | The home folder |
| Disks | Partly: `OSYR_MEDIA` moves the mount folder; ejecting needs the real helper | `/run/media`, `sudo` |
| Browser | Yes with a headless browser (`OSYR_BROWSER`, `OSYR_WEBDRIVER`); needs pages served locally for repeatable tasks | WebDriver |
| Looking at the screen | Yes with a fixed image (`look --file`) and a vision server | `grim`, the model |
| Sound | **No** | `wpctl`, `pw-dump`, `pw-play` |
| Wifi and network | **No** | `iwctl`, `busctl`, `ip` |
| Bluetooth | **No** | BlueZ over D-Bus |
| Displays | **No** | `swaymsg` |
| Reminders | **No**: they are systemd timers | `systemd-run`, `systemctl` |
| Backups | Partly: `rsync` to any folder | `rsync` |
| Voice | Partly: recognition and speech run on files; recording does not | `pw-record` |

Two ways to close the gaps, to be decided when the sandbox is designed: fake programs on the `PATH` (a `wpctl` that keeps its state in a file), which is cheap and works in the native runner; or the real services inside the container (PipeWire with a null sink, `mac80211_hwsim` for wifi), which is faithful but heavier and needs a kernel module on the host. Reminders, and later services and processes, need systemd inside the container in either case.

## Resetting between tasks

In the native runner each task gets a fresh home folder and a fake `/sys`; the provisional measurement of the model candidates already works this way. That covers what exists today. With the shell, tasks change the system outside the home folder, and the reset has to be a disposable container per task.

## Missing before the freeze

From the plan, in its priority order. Each one, when built, adds its commands, its read-back and its simulation to the tables above.

1. Shell with protections ([SHELL-DESIGN.md](SHELL-DESIGN.md), proposal).
2. Installing applications (`osyr ext`, `osyr box`); opening and closing applications.
3. Writing and editing files, with undo of the agent's last change.
4. Handing a task to the server model (`ask_server`).
5. Then: programming tools, music and video, office documents, VPN and firewall, camera and microphone, screenshots on request. Last: mail, printer and scanner.
6. For the benchmark: fixed temperature and seed in `osyr-chat`, and the fixtures of the table above.

## Still to be answered by Carlos

- Which of the CLI commands the agent is not told about should be added to its list.
- Whether the browser and looking at the screen are part of the training or only the control of the system.
- The ten things an ordinary user will ask most in a week, to check that each has a command.
- Whether the catalogue of real failures of the previous agent exists in writing, and where.
