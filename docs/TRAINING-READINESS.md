# Training readiness — inventory

13 November 2026, brought up to date on 23 November · **Partial.** Stage 0 of [TRAINING-PLAN.md](TRAINING-PLAN.md). This is the part the repository can answer by itself, at the code of 23 November (after v0.33.0). It becomes the frozen description of the interface when stage 0 ends.

## Verdict today

The "first priority" features of the plan are all built: the shell with protections, installing software (`osyr box`), opening and closing applications (`osyr app`), writing and editing files with undo (off by default), and handing a task to the second model (`ask_server`). The pretend hardware and the disposable session exist, and so does a runner for the benchmark. OSYR is **not yet ready to freeze**: the small model is not chosen, the fixed prompt is over its limit, `ask_server` has not run with real models, the "then" features (VPN and firewall, camera and microphone, screenshots on request) are not built, and the benchmark's format and families wait for approval.

## What the model sees

| Piece | State |
| --- | --- |
| Tools | `osyr(command)`, `shell(command, timeout)`, `clipboard(action, text)`, `remember(text)`, `look(question)`. Off by default: `write(path, content)` and `edit(path, old, new)` (`OSYR_WRITE=1`). Only for the local model and only when a second model is set up: `ask_server(task)` |
| Fixed prompt, measured on 23 Nov with the 4B's tokenizer | **2,150 tokens** with the shell (1,825 without it; 2,503 with the write tools too). With `ask_server` offered, 133 more: 2,283. The limit is 2,000 until the model is fine-tuned and 1,500 after: **it is over by 150**, from the lines added since 13 Nov (`box`, `app`, `ai advise`, `ai local`) |
| Commands inside `osyr` | The list is written in the tool description; anything else is refused before running (`USAGE`) and goes back to the model with a hint |
| Confirmation | Every `osyr` command that is not in the read-only list, and every shell command that changes something, asks: yes, always (a saved permission), or no. A "no" is final for the rest of the turn. Sensitive commands ask a second time with what the command explains (`CONFIRM_REQUIRED`). Destructive shell commands are blocked (`BLOCKED`) |
| Tool output | JSON, trimmed to 4,000 characters; the shell's whole output is saved to a file the result names |
| A turn | Up to 12 answers of the model; the same call with the same result is not answered twice; one `look` per turn |
| Sampling | The server's defaults, unless `OSYR_TEMPERATURE` and `OSYR_SEED` are set: the benchmark sets temperature 0 and one seed per run |

## Commands the CLI has and the agent is not told about

The tool description is shorter than the CLI. If these should be usable by the agent, they have to be in the description before the freeze; if not, the benchmark must not expect them.

| CLI command | In the agent's list |
| --- | --- |
| `bt on`, `bt off`, `bt disconnect`, `bt remove` | No (only `status`, `scan`, `connect`) |
| `audio volume --mic`, `audio mute --mic` | No |
| `sys power apply --profile` | No (only reading `sys power`) |
| `ui display mode`, `ui display off`, `ui display on` | No (only `list`, `extend`, `mirror`) |
| `box update`, `box reset`, `box status` | No |
| `ai gpu on|off`, `ai local get|use|remove` | No, on purpose: the user types `/gpu` and `/model` |
| `ai add`, `ai remove`, `ai second` | No, on purpose: adding a provider goes through `/pair` |
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
| `box install`, `box remove` | `box list` |
| `app run`, `app stop` | `app list` |
| `write`, `edit` | `files read`; `files undo` puts the previous version back |
| A shell command | Another shell command; the benchmark's verifier looks at the files themselves |

## Can it run without real hardware?

This decides how many instances can run in parallel in containers.

| Area | Without hardware today | How |
| --- | --- | --- |
| Brightness, temperature, battery and power | **Yes** | A pretend `/sys` (`OSYR_SYSFS`, or the session's own `/sys`) |
| Files, memory, help | **Yes** | The session's home folder, in memory |
| Sound, wifi and network, Bluetooth, displays | **Yes, since 23 Nov** | The doubles of `tests/doubles` (`wpctl`, `pw-dump`, `pw-play`, `busctl`, `iwctl`, `ip`, `swaymsg`), with the machine in one JSON file per scenario |
| The shell | **Yes** | Inside the disposable session (`sandbox/session`): no network, a read-only system, nothing kept |
| Disks | Partly: `OSYR_MEDIA` moves the mount folder; ejecting needs the real helper | `/run/media`, `sudo` |
| Browser | Not in the session: it has no network and no display. With a headless browser and pages served locally it could | WebDriver |
| Looking at the screen | Yes with a fixed image (`look --file`) and a vision server; not in the session today | `grim`, the model |
| Reminders | **No**: they are systemd timers | `systemd-run`, `systemctl` |
| Services, processes, disk usage, users | **No**: the session has no systemd and no root. They need doubles of their own, or the real system in a virtual machine | `systemctl`, `journalctl`, `df`, `useradd` |
| Installing software and applications | **No** in the session (`podman` needs what the container does not have); tested in the virtual machine | `podman`, `sway` |
| Backups | Partly: `rsync` to any folder | `rsync` |
| Voice | Partly: recognition and speech run on files; recording does not | `pw-record` |

The administration tasks of level 2 and 3 (services, logs, users, disks) are the gap that matters: they are the goal of the plan and nothing simulates them yet. Two ways: doubles for `systemctl`, `journalctl`, `df` and the rest, which is cheap and runs hundreds at once but is only as real as the doubles; or the real image in a virtual machine per task, which is faithful and slow without KVM on the VPS. To be decided with the task format.

## Resetting between tasks

One disposable session per task: the image's root read-only, a home folder in memory, the pretend machine created from its scenario. Everything is gone when it ends. The benchmark's runner works this way (`bench/run.py`).

## Missing before the freeze

1. The fixed prompt under its limit: 150 tokens to cut with the shell, 283 with `ask_server`.
2. `ask_server` run with real models (the 4B handing over to the 35B), and its place in the prompt measured.
3. The small model chosen (4B or 2B), by measuring on real tasks.
4. The "then" features that touch the system: VPN and firewall, camera and microphone, screenshots on request. Programming, music and video and office documents are covered by installing programs in the box.
5. A way to run administration tasks without a real system (see above).
6. The decisions below.

## Still to be answered by Carlos

- Which of the CLI commands the agent is not told about should be added to its list.
- Whether the browser and looking at the screen are part of the training or only the control of the system.
- The ten things an ordinary user will ask most in a week, to check that each has a command.
- Whether the catalogue of real failures of the previous agent exists in writing, and where.
