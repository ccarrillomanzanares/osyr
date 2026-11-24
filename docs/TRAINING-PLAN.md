# Training plan: features, benchmark and fine-tuning

12 November 2026 · Carlos Carrillo

## Summary

The goal is for the OSYR agent to be as capable as a person who uses and administers Linux. A small fine-tuned model covers user tasks and routine administration locally on 16 GB machines. The fine-tuned Qwen3.6-35B covers hard administration, on the server or on machines with 32 GB or more.

To get there the plan spends the training budget: the share of the Lambda credits assigned to this plan, which expire in November 2027. Another share of the credits is reserved for another project and this plan does not touch it. Every percentage in this document refers to the training budget.

Three things differ from the original design in `ARCHITECTURE.md`:

1. **What is missing gets built before training.** Today the agent only handles settings; it lacks a shell, writing files, installing software and managing services. The model learns the interface that exists on training day.
2. **The official minimum goes from 8 GB to 16 GB**, with AVX2. It becomes the public requirement when the fine-tuned model ships; until then the published images still run on 8 GB as they do today.
3. **Training is split into two rounds** (half and a third of the budget) with a period of real use in between, so that whatever is missing is found while there is still budget left.

This document is meant to be read by the agent working on the `osyr` repository at the start of every work session.

## Decisions taken (12 Nov 2026)

| Subject | Decision |
| --- | --- |
| Goal | An agent at the level of a Linux user and administrator |
| Official minimum | 16 GB with AVX2. 8 GB stops being a target: the model may start there, but with no benchmark and no guarantee |
| The 2014 laptop (no AVX2) | Out of the plan |
| Small model | To be chosen between Qwen3.5-4B and a somewhat larger one, by measuring untrained candidates on the VPS |
| Level 3 | Given by the fine-tuned 35B: on a server, or locally with 32 GB or more |
| Machines with 32 GB or more | Only the 35B is loaded; revisited if it turns out slow for simple requests |
| The second model (13 Nov) | The 35B and an external provider are the same thing: the second model the agent consults when the local one is not enough is a provider added with `osyr ai add` or `/pair`. Carlos's 35B is one such provider, served with `llama-server` (the llama-hardened project). Users can be offered the same: their own VPS or similar with llama-hardened, with the fine-tuned 35B downloaded from ccmai.org |
| Handing a task to the second model (13 Nov) | The data leaves the machine, so it asks with the three usual answers: yes, always for that provider, no |
| Installing software (13 Nov) | With containers (`osyr box`). The official extensions (`osyr ext`) are set aside for now. One default box with Debian stable, created by itself the first time; the user never has to know what a container is. More boxes or other distributions are an advanced option. The programs in the box see the home folder of the user who runs them (any user of the machine, each with their own box, not only `osyr`), except what holds keys and passwords. The box starts empty: what is asked for gets installed |
| The "then" features (13 Nov) | Programming, music and video and office documents are covered by installing programs in the box. Built apart: only what touches the system (VPN and firewall, camera and microphone, screenshots on request) |
| Graphical applications (13 Nov) | They open in another workspace, as in `ARCHITECTURE.md`; the agent's console stays the main screen and the agent says how to come back |
| The small model on the minimum profile (14 Nov) | The untrained 4B is too slow on the HP 15s, the reference 16 GB laptop (about 32 s for a simple request), so the Qwen3.5-2B joins the 4B as a candidate and the wait on that laptop is a criterion of the choice. The 0.8B is ruled out: about a third of the provisional tasks ([TRAINING-LOG.md](TRAINING-LOG.md)) |
| Choosing where the model runs (14 Nov) | The person chooses from a menu, with OSYR's recommendation marked: `osyr ai advise` measures the machine and gives the wait in seconds. Shown at the first start and with `/ai advise`. The choices for machines Carlos does not have (32 GB or more, graphics cards) are built anyway and published as untested on that hardware; failures come in as issues on GitHub |
| The large model on the user's machine (14 Nov) | Offered now with the base Qwen3.6-35B, downloaded from Hugging Face into `/home` (`osyr ai local get large`), for machines with 32 GB or more. It is replaced by the fine-tuned one, served from ccmai.org, when that exists |
| Publishing (13 Nov) | No intermediate images: the next one is published when all the pending changes are done. Commits are pushed as they are made |
| Shell | With protections, for both models. The pilot decides whether the small one keeps it |
| The 35B on the VPS (llama-hardened) | May be queried for tests, without touching its configuration |
| Training | Two rounds with real use in between |
| Local GPU | Built on 14 Nov for AMD and Intel cards through Vulkan (`osyr ai gpu on`), untested on a discrete card; NVIDIA with the proprietary driver stays for later, with no date. It blocks nothing in this plan |
| Lambda storage (23 Nov) | Not used, at any stage. The credits do not cover it: a persistent filesystem would be charged to the card. Everything stays on the instance's own disk and is copied out before the instance is terminated |
| Task families (23 Nov) | A benchmark task is a family: one situation with several sampled variants (names, values, wording). Whole families are held out, and a gate is read with its interval over the variants. With 30–50 single tasks the held-out group would be 6 to 10 tasks, and the pilot's 10 points would be one task |
| What the pilot covers (23 Nov) | Every mechanism of the first round gets a minimal run in the pilot, the 35B's fine-tuning, the on-policy distillation and the RL included, and the pilot measures how busy the rented GPU is. Their real cost must not be seen for the first time inside the 50 % round |
| Handing over, measured both ways (23 Nov) | Handing a task over when the small model could have done it counts as a failure too: it sends data off the machine and adds a wait. The examples of "this is beyond me" come from what the fine-tuned small model is measured to fail, not from a guess |

## Levels and profiles

| Level | What it is | Examples |
| --- | --- | --- |
| 1. User | Settings, files, browser, applications | Raise the brightness, join a wifi network, install a program, find a document |
| 2. Routine administrator | Tasks of a few steps with a known procedure | See why a service fails and restart it, free disk space, create a user, mount a disk |
| 3. Diagnosing administrator | A new problem, many steps, hypotheses | Read long logs, find the cause and fix it without breaking something else |

| Profile | Machine | Local model | Level |
| --- | --- | --- | --- |
| No local model | Less than 16 GB or no AVX2 | None guaranteed; remote provider through `/pair` | The provider's |
| Minimum | 16 to 31 GB with AVX2 | Fine-tuned small model | 1 and 2; 3 with a server |
| Higher | 32 GB or more, or a server | Fine-tuned 35B | 3 |

The small model has to learn to recognise that a task is beyond it and hand it to the 35B. That is a central part of the training, not an extra. The examples for it are the tasks that the fine-tuned small model fails and the teacher solves, taken from the on-policy runs of phase 4.3; and the benchmark checks the other side too, that it does not hand over what it can do (23 Nov).

## Starting point

The system and the agent exist (v0.31.0). The administration features, the benchmark, the sandbox and all the training code are missing.

| Piece | Status on 12 Nov 2026 |
| --- | --- |
| Image, installer, A/B updates | Done and tested in QEMU and on two HPs |
| `osyr` CLI with JSON output | Done: network, sound, voice, brightness, displays, Bluetooth, reminders, disks, backups, finding and reading files, help, memory, browser, updates |
| Agent (`osyr-chat`) | Done, with four tools: `osyr`, `clipboard`, `remember` and `look`. Fixed prompt of about 960 tokens |
| Shell, writing files, installing software (`osyr ext`, `osyr box`), applications and services | Do not exist. They are in the design in `ARCHITECTURE.md` |
| Tests (`tests/run.py`, `make test`) | System tests. Only a few measure the agent: `agent-reads-with-tool`, `agent-asks-before-change`, `agent-change-applied`, `memory-saved`, `memory-recalled`, `look-sees-screen` |
| Simulated hardware for tests | Partial: `OSYR_SYSFS` gives a fake `/sys` used by `ui`, `power`, `thermal` and `sys`. `audio`, `net`, `display` and `bt` call real programs |
| Since then (23 Nov) | Doubles for `audio`, `net`, `display` and `bt` (`tests/doubles`) and a disposable container session with a model gate (`sandbox/`), built ahead for the web sandbox; see `MILESTONE-0.md` |
| Real conversations | Saved in `~/.local/share/osyr/chats/*.json`; `osyr-diag` can send them (`SEND_CHATS`) |
| Benchmark, sandbox and training scripts | Do not exist |
| 35B server | Runs on the VPS on CPU (the llama-hardened project); its speed is to be measured |
| `llama.cpp` with GPU | No: it is built for CPU only |

The agent tests already in the native phase are the seed of the benchmark: they start the real model, give it a request and check the final state.

## Working rules

These rules hold in every session and weigh more than the urge to move forward.

1. **Read before proposing.** At the start: `CLAUDE.md`, `docs/ARCHITECTURE.md` (section "Model and training"), `docs/MILESTONE-0.md` and `docs/TRAINING-LOG.md`. Do not repeat tests or decisions that are already written down.
2. **One stage at a time.** A stage does not start until the gate of the previous one is met. No phase is accepted without improving on the benchmark.
3. **No spending without permission.** Before starting a Lambda instance: say which instance, how many hours and what share of the budget it is, and wait for Carlos's go-ahead. Shut the instance down at the end and check that it is off.
4. **Record spending and results** in `docs/TRAINING-LOG.md`: date, instance, hours, share of the budget spent and benchmark numbers. Amounts of money are given to Carlos in the conversation and are not written in the repository.
5. **Measure, do not assume.** Every claim about the model carries its benchmark number. If something has not been run, say so.
6. **Everything in English inside the repository.** The conversation with Carlos is in Spanish, one question at a time.
7. **Leave the state written down** when each piece is finished, in the same session: system features in `docs/MILESTONE-0.md`, training in `docs/TRAINING-LOG.md`.
8. **Carlos's decisions are not taken for him.** The ones in the last section are asked.
9. **The 35B on the VPS belongs to another project.** It is queried for tests; its configuration is not touched and its server is not stopped.
10. **No Lambda storage.** The credits do not cover it and it would be charged to Carlos's card. Never create a persistent filesystem and never start an instance with one attached. Models, data and checkpoints live on the instance's own disk, which is erased when the instance is terminated: copy out what must be kept (to the VPS or to Hugging Face) before shutting down, and download it again on the next instance. When checking that an instance is off, check too that the account has no filesystem.

## Stage 0: build what is missing (no spending, November to February)

Result: the agent has every capability that will be trained, even if the untrained model uses them badly.

### Shell with protections

The `osyr` commands remain the first choice; the shell is for what they do not cover. The protections are enforced by the system, not by the model:

- What only reads goes through without asking.
- What changes something asks for confirmation, showing the command.
- What is destructive (deleting outside the user's folder, formatting, touching the boot) is blocked.
- The output is trimmed before the model sees it.

With the shell, these tasks need no command of their own; the programs only have to be in the image and the benchmark has to measure them: files (copy, move, compress), processes, services and logs, disk space and external disks, network diagnosis and SSH, users and permissions, scheduled tasks, date and language, shutting down and suspending.

### Features to build, in priority order

| Order | Feature |
| --- | --- |
| First | Shell with protections · installing applications (`osyr ext`, `osyr box`) · opening and closing applications · editing documents and configuration · undoing the agent's last change · handing the task to the 35B (`ask_server`) |
| Then | Programming (git, Python, scripts) · music and video · office documents · VPN and firewall · camera and microphone · screenshots on request |
| Last | Mail · printer and scanner |
| Second round | Driving graphical applications with clicks and keyboard |

**The March rule:** if not everything is there in March, training goes ahead with what exists and the rest moves to the second round.

### Choosing the small model

Two or three candidates are downloaded to the VPS and measured untrained with the `llama-server` of the OSYR image (not inside the llama-hardened Docker): successes on the first tasks, memory they take and speed with 4 threads. The real speed on a 16 GB laptop comes from `osyr-diag` when OSYR is booted there from USB.

### Simulated hardware

Doubles for `audio`, `net`, `display` and `bt` to test without real hardware, as `OSYR_SYSFS` already does for the brightness. The system must also be put into a given initial state and reset after every task.

### Freezing the interface

At the end of the stage `docs/TRAINING-READINESS.md` is written: tools, list of commands, list of read-only commands, fixed prompt and confirmation protocol, as they stand for training.

After the freeze a change is possible, but it forces the baseline to be measured again and the affected data to be regenerated. To make changes cheap, the model is trained to look up `osyr doc` before using something it does not know, and the benchmark measures it with commands it never saw in training.

**Gate:** the shell and the "first" priority features work and are tested in QEMU, the small model is chosen and the interface is frozen in writing.

## Stage 1: first benchmark tasks and real use (no spending, until March)

Result: 30 to 50 tasks with a final-state verifier, run with a single command, and a list of what Carlos misses when using the system.

1. **Define the task format.** One file per task with: initial state, user request, language, mode (text or voice), level (1, 2 or 3), verifier and expected behaviour (for example, asking for confirmation before changing something). A task is a family: besides the language and voice variants, the same situation is sampled into several variants (file names, values, the wording of the request), so that a score has an interval and the training data is not the same thirty conversations repeated (23 Nov; [BENCH-TASK-FORMAT.md](BENCH-TASK-FORMAT.md) describes it).
2. **Write the runner.** It starts from the native phase of `tests/run.py`. It saves the full trajectory (messages and tool calls) as JSON. The verifier looks at the final state and the trajectory, never at the terminal text. The runner sets the temperature to 0.
3. **Write the tasks** by category:
    - Reading without changes and changes with confirmation (what exists today).
    - Administration: services, logs, disk, users, network, processes.
    - Files, applications and installation.
    - Safety: the user says no, the request is ambiguous, the request asks for something dangerous, the command is blocked.
    - Escalating: tasks the small model must hand to the 35B. Measured both ways: every task outside this category expects no hand-over.
    - Commands never seen, solved by looking up `osyr doc`.
    - Real failures already catalogued: loops, `sudo`, repeated polling, claiming something that did not happen.
    - Several steps, languages (several of the seven) and voice mode (text with transcription noise).
4. **Hold out 20 % of the tasks** from day one. They are not used for training or for writing variants, at any stage. Whole families are held out: no variant of a held-out family is ever seen in training.
5. **Measure untrained** the small model and the 35B on the VPS: first baseline, with a score per category and per level.
6. **Use OSYR for real** (March): Carlos uses it from USB with the VPS 35B as the agent and notes what is missing. The saved conversations serve as examples of how people talk.

**Gate:** `make bench` (or equivalent) runs every task and gives a score per category and level, with the mean and spread of three runs. The uncertainty of a score comes from the variants, not from repeating a run: at temperature 0 the three runs of a task come out almost the same. The report gives each score with its interval over the families.

## Stage 2: pilot on Lambda (7 % of the budget, April)

Result: the whole plan in miniature, to learn early how far each model gets and what each step really costs.

**First, an infrastructure rehearsal** of about two hours, charged to this stage's share and with the usual go-ahead: start an instance, serve the 35B, run a few tasks from the VPS against it, copy the results out, terminate and check. On-demand capacity is first come, first served and most of the budget is spent in two bursts, so a capacity or setup problem should show here and not in the middle of a measurement. It may be run earlier, once the runner exists, if Carlos decides so.

1. **GPU baseline** of the small model and the 35B. The task environment runs on the VPS and calls the model served on Lambda. Measure how busy the instance is: the share of its time in which the model is generating. Lambda bills by the minute whether or not the model has work, so this number decides how many environments run in parallel and whether a cheaper instance would do.
2. **Teacher trajectories.** The untrained 35B solves the tasks several times; only those the verifier approves are kept.
3. **Short fine-tune of the small model** (LoRA) with those trajectories, without the held-out tasks.
4. **Measure again**, with and without the shell, above all on the held-out tasks.
5. **Close the loop:** export to GGUF Q4_K_M, load it into the OSYR image and run the benchmark. The comparison that counts is base model against fine-tuned model, both in Q4_K_M.
6. **A minimal run of what is left of the first round:** fine-tuning the 35B (phase 4.1), the on-policy distillation (4.3) and the RL (4.4). Minutes each, not to improve anything: to measure memory, speed and that the plumbing works, so that their shares of the budget are set from measurements. This also fixes the instance type of each phase: the 35B is about 70 GB of weights at 16 bits, so on an 80 GB card it only fits quantized, and the 180 GB card costs about twice as much per hour.
7. **Pilot report:** numbers before and after, real hours and cost, how busy the GPU was, the instance type each phase needs, the level each model reaches and what changes in the budget.

**Gate:** the fine-tuned small model beats its baseline by at least 10 points on the held-out families (mean of three runs), the interval of that difference over the held-out variants leaves zero out, and the exported model works on the 16 GB profile. If it does not improve, work stops and the next step is decided with Carlos before spending more.

## Stage 3: full benchmark and sandbox (2 %, May)

Result: 300–500 tasks and hundreds of disposable instances in parallel.

1. **Grow to 300–500 tasks**, keeping the held-out group intact. Every new system feature comes in with its tasks.
2. **Sandbox in containers:** the OSYR image as a disposable container, with the initial state of each task and the verifier inside.
3. **Parallel execution** on CPU: on the VPS or on CPU credits from another offer (Scaleway or AWS), not on GPU hours.
4. **Official baseline** of both models with the full benchmark. The Lambda spending of this stage is for serving the 35B on GPU during this measurement.
5. **Optional:** paraphrases and language variants with the Nemotron tokens on DeepInfra, after checking their licence.

**Gate:** measured baseline of both models with the full benchmark.

## Stage 4: first training round (50 %, June and July)

Every phase ends by measuring on the benchmark, and the next one does not start unless it improves. The percentages are indicative and are revised with the real costs of the pilot.

| Phase | What is done | Gate | Share of the budget |
| --- | --- | --- | --- |
| 4.1 Fine-tune the teacher | SFT of the 35B with the verified trajectories of the pilot and a first batch generated with the base 35B | The fine-tuned 35B beats the base 35B | 13 % |
| 4.2 Verified data | The teacher solves tasks in hundreds of instances; only those the verifier approves are kept | Data covering every category, level and language | 17 % |
| 4.3 SFT and distillation | From the teacher to the small model, with on-policy distillation | The small model beats its baseline | 12 % |
| 4.4 RL in a sandbox | GRPO on the small model with a verifier reward and a safety penalty | Improves on 4.3 without worsening the safety tasks | 8 % |

At the end: export to GGUF, run the benchmark on the 16 GB profile and publish an image with the fine-tuned model.

## Stage 5: real use with the fine-tuned model (no spending, August)

Carlos uses OSYR with the fine-tuned model and notes what is missing and what fails. That gives the list for the second round: new features, weak categories and tasks that were not in the benchmark.

## Stage 6: second round (33 %, September)

The expensive parts of the first round (benchmark, sandbox, fine-tuned teacher and data) are reused. The second round generates data for whatever is missing, repeats the distillation and, if it pays off, extends the RL or adds the driving of graphical applications.

**Gate:** the full benchmark passes on the 16 GB profile with the small model and on the higher profile with the 35B, each at its levels.

## After training

System work that does not affect what the model learns:

- **Choosing the profile at install time:** OSYR measures memory, AVX2 and speed, and proposes the model. If the 35B is too slow, it recommends the small one even when there is memory.
- **Downloading the 35B** (about 20 GB) only on machines that reach the higher profile.
- **Local GPU:** `llama.cpp` with Vulkan first; NVIDIA with the proprietary driver as an extension. No date, because there is no GPU hardware to test it on. Until then, whoever has a GPU connects their own server with `/pair`.
- **Agent auditor:** a kernel-level log of what the agent does. An idea on record, with no date.

## Budget and calendar

The Lambda credits expire in November 2027. All spending ends in September and October is left as margin.

| When | Stage | Share of the budget |
| --- | --- | --- |
| Nov 2026 – Feb 2027 | 0. Build what is missing, choose the small model. 1. First tasks | 0 |
| Mar 2027 | 1. Real use with the VPS 35B | 0 |
| Apr 2027 | 2. Pilot | 7 % |
| May 2027 | 3. Full benchmark and sandbox | 2 % |
| Jun – Jul 2027 | 4. First round | 50 % |
| Aug 2027 | 5. Real use with the fine-tuned model | 0 |
| Sep 2027 | 6. Second round | 33 % |
| Oct 2027 | Margin and reserve | 8 % |

Confirmed in the Lambda account on 23 Nov 2026: the credits expire around 4 November 2027 (the account showed 346 days left; the hour and the time zone are not known) and they pay for on-demand instances. **Storage is not covered and would be charged to the card**, so it is not used (working rule 10). Lambda's terms also let it withdraw credits at any time, one more reason to keep October as margin and not to leave the important work for the end.

## Carlos's pending decisions

- [ ] Choose the small model once the candidates are measured.
- [x] Approve the shell design: what is blocked, what asks for confirmation and what goes through. Approved on 13 Nov 2026 as built ([SHELL-DESIGN.md](SHELL-DESIGN.md)); it can still be adjusted until the interface is frozen.
- [ ] Approve the task format and the list of categories before the 30–50 tasks are written. Waits until the features are built (13 Nov): the tasks depend on the interface that is left. **The list of families was approved on 24 Nov 2026** (forty-five, in [BENCH-TASK-FORMAT.md](BENCH-TASK-FORMAT.md)); the format's own questions (variants per family, the held-out share, judged checks) are still open.
- [ ] Go-ahead for every Lambda instance start (instance, hours and share of the budget).
- [ ] After the pilot: whether the small model keeps the shell and whether to go on with it.
- [ ] Where the sandbox instances run: VPS, Scaleway or AWS.
- [ ] Whether to use the Nemotron tokens to generate requests, once the licence is checked.
- [ ] After the first round: what goes into the second.
- [ ] Whether to apply for the GPU-hours offer for European startups, which would extend the RL without touching Lambda.
- [ ] With the task format: how many variants a family has and how many families are held out (proposed: about ten variants per family, so that the held-out group holds about a hundred).
- [ ] When the infrastructure rehearsal runs: at the start of the pilot, or earlier once the runner exists.
