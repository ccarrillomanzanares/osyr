# Training log

What was measured, decided and spent, in order. Spending is written as a percentage of the training budget ([TRAINING-PLAN.md](TRAINING-PLAN.md)).

## 13 November 2026 — first look at the small-model candidates

**Spent: 0 %.** Run on the VPS, no rented machine.

**Not a benchmark.** Fifteen provisional tasks on what the agent can do today (brightness, memory, files, temperature, power, help), three runs each. It was made to compare speed and memory and to try the harness, and it ended up saying more about the interface than about the models.

### How

- OSYR's own `llama-server` and the real `osyr-chat`, driven through a terminal; each task in a fresh home folder with a fake `/sys`.
- 4 threads on an AMD EPYC virtual machine, context 8,192, two slots, the server's default sampling (not temperature 0 yet).
- A task passes on the final state and the saved conversation, not on the text of the terminal.
- The harness and the transcripts are in `local/candidates/` (not in git).

### Result

| Model | File | Passed (of 45) | Passed, corrected¹ | Memory in use | Reading the prompt | Writing | Median task |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen3.5-4B Q4_K_M (today's) | 2.7 GB | 40 | 41 | 4.6 GB | 27.9 tok/s | 7.5 tok/s | 29 s |
| Qwen3.5-4B Q8_0 | 4.5 GB | 40 | 40 | 4.9 GB | 29.5 tok/s | 6.4 tok/s | 32 s |
| Qwen3.5-9B Q4_K_M | 5.7 GB | 37 | 40 | 8.6 GB | 16.9 tok/s | 5.4 tok/s | 42 s |
| Qwen3.5-9B Q6_K | 7.5 GB | 38 | 41 | 7.8 GB | 12.7 tok/s | 4.7 tok/s | 51 s |

¹ One task (how to update OSYR) demanded a call to `osyr doc`. Every model gave the right answer in all three runs; the 9B gave it without the call, because the prompt already says it. The corrected column counts the answer.

Memory is what the server held at the end of the run; it is approximate (the 9B Q4 shows more than the Q6, which is the page cache, not the model).

### What it says

1. **These tasks do not separate the models.** Twelve of the fifteen were passed by all four in every run. With the correction the four are within one task of each other, which is noise with three runs.
2. **The 9B costs about 1.5 times the wait and twice the memory** of the 4B, and on tasks this simple it buys nothing. Both fit in 16 GB. Whether it earns its cost can only be seen on harder tasks (several steps, shell, diagnosis), which do not exist yet.
3. **A higher quantization changed nothing visible** for either size.
4. Speed on the VPS is not speed on a laptop: the figures compare the candidates with each other, nothing more.

### The failures were mostly the interface, and the same for every model

| What happened | Seen in | Whose problem |
| --- | --- | --- |
| "It is written in my file nota.txt": the model calls `look` (the screen) first, up to five times, and runs out of its six calls | 11 of 12 runs | Tool descriptions: nothing tells `look` apart from reading a file |
| `files read nota.txt` fails because the file is in `Documents`; it takes a `find` and a second `read` | Most runs | CLI: a bare name could be searched for |
| `files find factura de la luz` finds nothing for `factura-luz-marzo.txt`: every word must match, "de" and "la" included, and a quoted phrase is one word | 10 of 12 runs | CLI: matching too strict |
| After that, the 4B follows the hint (`--content`) and finds it; the 9B stops and asks the user | 9B: 5 of 6 runs fail | Model, and the reason for the 9B's lower score |
| The user says no, and the model asks again for the same command | 4B Q8: 2 of 3 runs | Model |
| A read followed by a change: only the read is done | 4B Q4: 1 of 3 runs | Model |

The first three are worth fixing in the product before any training: they cost nothing and no fine-tuning should spend its capacity learning around them.

### Fixed the same day

The three interface problems were fixed and the two file tasks measured again (4B and 9B in Q4_K_M, three runs each):

| Task | Before | After |
| --- | --- | --- |
| Find the electricity bill | 4B 3 of 3 (with a second search), 9B 0 of 3 | 3 of 3 both, one call |
| Read `nota.txt` | 4B 0 of 3, 9B 1 of 3 | 3 of 3 both, one call |

- `files find` drops words of one or two letters, splits quoted phrases, ignores accents, and offers the closest names when none has every word.
- `files read` looks a bare file name up in the user's folders.
- Saying in the description of `look` that it is not for files was not enough: the models still looked at the screen first. A sentence in the fixed prompt was needed.

### Next

- No choice between 4B and 9B yet. Measure again when the shell and multi-step tasks exist, with the real task format, temperature 0 and the 35B as a reference.
- Until then the default stays the 4B Q4_K_M.

## 13 November 2026 — the shell, and the write tools, with the untrained 4B

**Spent: 0 %.** Same harness and the same limits as the entry above (provisional tasks, the VPS, the server's default sampling). Model: Qwen3.5-4B Q4_K_M, three runs per task. Nine provisional shell tasks were added to the fifteen, and three for writing files.

### Result

| What the agent has | Tools | Fixed prompt | The 15 earlier tasks | The 9 shell tasks |
| --- | --- | --- | --- | --- |
| As in v0.31.1, with the fixes of the entry above | 4 | 1,640 tokens | 40 of 45 before the fixes; not run whole after them | — |
| Plus `shell` | 5 | 2,030 | 42 of 45 (43 in an earlier run) | 25 of 27 (both runs) |
| Plus `shell`, `write` and `edit` (a subset of 13 tasks) | 7 | 2,380 | reading a file by its name: **0 of 3** | making a folder: 1 of 3 |

### What it says

1. **The shell is worth having, even untrained.** Disk space, the biggest file, the process using most memory, making a folder and moving a file passed in almost every run, and the model kept using `osyr` for the brightness in every run (the "preference" the design asks for).
2. **The protections held.** Asked for its private SSH key, the model tried `cat` once, got `BLOCKED`, and explained without looking for another way (3 of 3). Asked to delete the whole home folder, it never proposed the command (3 of 3).
3. **Seven tools are too many for the untrained 4B.** With `write` and `edit` added, the question "which room is the meeting in? It is in my file nota.txt" was answered, in six runs of six, by **creating a new `nota.txt` with an invented room** and answering from it. Telling it in the description ("only when the user asks to create or change a file") changed nothing. Making a folder got worse too. The write tools are therefore built and tested but **off by default** (`OSYR_WRITE=1`), until the fine-tuned model or for the server model.
4. **The fixed prompt was never 960 tokens.** Measured on the server it was about 1,640 at v0.31.1, over the limit of 1,500. The limit is now 2,000 until the model is fine-tuned, and 1,500 after.

### Failures worth a benchmark task each

| What happened | How often | Whose problem |
| --- | --- | --- |
| The user says no, and the model asks for the same command again (up to four times; once it answered "tell me the password and I will do it") | 2 of 6 runs of the two "declined" tasks | Model. The system now makes a "no" final for the rest of the turn; measured after: asked once in 6 of 6 runs |
| "Look at the brightness and, if it is under 60 %, set it to 90 %": it reads, changes nothing, and says it did | 4 of 6 runs | Model: claims an action it did not take |
| "Delete the file recetas.txt": it answers that it cannot delete files, without trying the shell | 2 of 6 runs | Model, and the prompt |
| A path written as `/Documents/x` for `~/Documents/x` | Several runs with the write tools | Fixed: the result now says where the user's folders are, instead of `BLOCKED` |
| It invents the home folder (`/home/user/…`) | 1 run | Model; the description now says the shell starts in `~` |

### Next

- A test of the shell inside the image, in the virtual machine: `sudo` with a real password and a real terminal.
- The benchmark needs tasks for the three model failures above; "says it did something it did not do" should be checked on every task that changes state.
- Each new capability has to fit in the 2,000 tokens: a new description means shortening another.

## 14 November 2026 — smaller candidates: Qwen3.5-0.8B and 2B against the 4B

**Spent: 0 %.** Same harness and the same limits as the entries above (provisional tasks, the VPS, 4 threads, the server's default sampling). Asked because the 4B is too slow on the user's laptops: would a smaller model do?

**Not a benchmark.** 27 provisional tasks, three runs each: the fifteen of the first entry, nine with the shell and three that write files. The agent as it stood that morning, with `shell`, `write` and `edit` on.

### Result

| Model | File | Passed (of 81) | Run by run (of 27) | Memory in use | Reading | Writing | Median task |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen3.5-0.8B Q4_K_M | 0.5 GB | 25 | 9, 7, 9 | 1.1 GB | 127 tok/s | 21.8 tok/s | 13.5 s |
| Qwen3.5-0.8B Q8_0 | 0.8 GB | 34 | 9, 13, 12 | 1.2 GB | 133 tok/s | 21.0 tok/s | 13.4 s |
| Qwen3.5-2B Q4_K_M | 1.3 GB | 58 | 18, 19, 21 | 2.1 GB | 68.5 tok/s | 16.3 tok/s | 15.0 s |
| Qwen3.5-4B Q4_K_M (today's) | 2.7 GB | 76 | 24, 26, 26 | 4.5 GB | 28.5 tok/s | 7.2 tok/s | 30.5 s |

The 4B's third run was cut after 16 tasks when the session ended; its last 11 tasks were run later the same day, with one line more in the list of commands (`ai advise`). Its speeds are from those 11 tasks.

Speed on the 2014 laptop (A8-7410, no AVX2, 4 threads), the only real hardware where all were measured:

| Model | Reading | Writing | Reading 2,000 tokens |
| --- | --- | --- | --- |
| Qwen3.5-0.8B Q4_K_M | 15–16.5 tok/s | 3.9–4.4 tok/s | 2 min 14 s |
| Qwen3.5-2B Q4_K_M | 7.4–7.7 tok/s | 2.5 tok/s | 4 min 32 s |
| Qwen3.5-4B Q4_K_M | 3.0 tok/s | 1.1–1.2 tok/s | 11 min 29 s |

### What it says

1. **The 0.8B is not an agent.** A third of the tasks. It fails one-step requests (setting the brightness in Spanish and in German: 0 of 3; answering without a tool: 0 of 3) and loops, up to 12 calls in a task. The commands are in its prompt, so this is not missing information, and retrieval would not give it what it lacks. Ruled out.
2. **The 2B is close on what the 4B does well and falls on the shell and on writing.** 40 of 45 on the fifteen first tasks; 18 of 36 on the shell and writing. The failures read are judgement, not format: `mkdir Documents\viaje` (a Windows path) reported as done; asked for the free space, it lists external disks, finds none and says there is no free space; asked to create a file, it writes it in `/tmp` and stops.
3. **The 2B is about twice as fast as the 4B** everywhere it was measured, and takes half the memory.
4. **The 4B fails one task in every run:** creating a file with `write` (0 of 3). The other 26 pass in two of three runs or more.

### Decision (the user's, 14 Nov)

The untrained 4B is not usable on the reference 16 GB laptop (HP 15s: 25.9 tok/s reading and 5.35 writing, about 32 s for a simple request), so the wait there becomes a criterion and the 2B joins the 4B as a candidate for fine-tuning. Whether a fine-tuned 2B reaches a useful level is not known: the pilot is to say. Training a small model from scratch was considered and discarded: what a small model lacks is general language and reasoning, which the budget cannot buy, not knowledge of OSYR.

### Next

- The 2B's speed on the HP 15s, from `osyr ai advise --measure` with that model loaded.
- In the pilot, fine-tune the 2B alongside the 4B and compare on the held-out tasks.
- The benchmark needs tasks for the 2B's three kinds of failure: a wrong path reported as done, an answer from the wrong command, a file written somewhere else.

## 23 November 2026 — the 35B on the VPS's processor, measured through the web sandbox

**Spent: 0 %.** The plan's starting point said "its speed is to be measured". Measured while connecting the web sandbox (`ccmai.org/osyr/try/`, see [MILESTONE-0.md](MILESTONE-0.md)) to it: the real `osyr-chat` in a disposable session, the base Qwen3.6-35B-A3B in Q4_K_M served by `llama-server` with 14 threads on the VPS (16 virtual cores, no graphics card). Nothing of that server was changed. Not a benchmark: a handful of conversations.

| What | Measured |
| --- | --- |
| Reading the prompt | 15 to 36 tokens per second |
| Writing, with an empty context | 7 to 8.5 tokens per second |
| Writing, inside a conversation of 2,000 to 3,000 tokens | 3 to 8 tokens per second |
| The fixed prompt (2,160 tokens), read for the first time | 150 s |
| A turn with one command, the prompt already read, one conversation at a time | 23 to 56 s |
| The same with two conversations at once | 40 to 80 s |
| A turn with reasoning left on and the prompt unread (the first try) | about four minutes |

### What it says

1. **The earlier figure of about 15 tokens per second for this server (13 Nov, from VirtualBox) does not hold** for writing: it is 3 to 8. It may have been the reading speed.
2. **Reasoning must be off** (`enable_thinking: false` in every request): with it on, the 35B spent most of each answer's tokens thinking. `osyr-chat` only sends that field to the local model, on purpose (a strict provider rejects fields it does not know), so a 35B added with `/pair` reasons on every answer. For OSYR's own server model this has to be settled before the real use of March: either the server turns it off by default or the chat is told that this provider takes the field.
3. **The prompt has to be read ahead.** The server keeps what it has read and reuses it when the next conversation starts the same way; the sandbox asks for the fixed prompt every 20 minutes so that no visitor waits the 150 s.
4. On this processor the 35B serves two conversations at once at the edge of what is bearable. As a teacher for trajectories or as the benchmark's reference it is slow on the VPS: about a minute per turn means hours for a few hundred tasks. That is what the Lambda instances of the pilot are for.
5. What it did in those conversations, all simple requests (wifi, volume, brightness, Bluetooth, displays, files): the right command in every one. Three slips worth a task each: it invented an address (`192.168.x.x`) the result did not give; it asked for `shopping_list.txt` when the file is `shopping-list.txt`; it wrote `Sony WH-1000XM5` for the `XM4` the scan had just listed. The last two were also the product's (fixed: a loosely matching file name is found, a name of several words needs no quotes).

## 23 November 2026 — first whole run of the benchmark's runner (example families, the 4B)

**Spent: 0 %.** **Not a benchmark:** eight example families written to try the runner ([BENCH-TASK-FORMAT.md](BENCH-TASK-FORMAT.md), still a draft), all of level 1. What this entry measures is the machinery.

### How

- `make bench`: Qwen3.5-4B Q4_K_M on the image's `llama-server`, 4 threads on the VPS, context 8,192, temperature 0, one run.
- Eight families, ten variants each, in seven languages, four of the eighty spoken. Each variant in its own disposable session, with the pretend hardware; judged on the machine's state before and after and on the saved conversation.
- The agent as it stood that evening: shell on, write tools off, the fixed prompt at 2,150 tokens.

### Result

| | Score | 95 % interval | Families | Variants |
| --- | --- | --- | --- | --- |
| All | 92.5 % | 83.7 to 98.8 | 8 | 80 |
| Train | 93.3 % | 83.3 to 100 | 6 | 60 |
| Held out | 90.0 % | 70.0 to 100 | 2 | 20 |

54 minutes; 36 s per variant (median), 1.2 tool calls per variant. No hand-over (there was no second model).

### The six failures, all the model's

| Family | Variants | What happened |
| --- | --- | --- |
| Delete a file | 2 of 10, both in French | "I cannot delete files from the chat": it never tried the shell. Catalogued on 13 Nov |
| Recall a name | 2 of 10, the same wording ("Remind me: my dog's name?") | It took "remind me" for "remember": saved the fact it already had, three times, and answered nothing |
| Read, then set | 1 of 10 | It read the brightness and said it had set it. It had not: no command, no question. Catalogued on 13 Nov |
| Join a wifi | 1 of 10 ("join the Casa-Lopez wifi") | It scanned and asked for the password in the chat, which the prompt forbids, instead of the connect command |

### What it says

1. **The machinery holds:** eighty conversations in sessions that start clean, no run lost, and every failure read by hand was a real one. No verdict had to be corrected.
2. **The variants do their job.** Four of the eight families pass in some variants and fail in others, and what the failing ones share can be read off: a language, a wording. With one conversation per task each of these would have been a coin: 100 % or 0 %.
3. **The interval is honest about how little eight families say:** 15 points wide. The pilot's gate of 10 points needs the hundred held-out variants the plan asks for, and more families than variants.
4. Three of the four failures were already in the list of 13 Nov; the benchmark finds them again without being told where to look.

### Next

- The same run after shortening the fixed prompt to 2,000 tokens, compared variant by variant.

## 24 November 2026 — the shorter prompt, and two administration families (examples, the 4B)

**Spent: 0 %.** Same conditions as the entry above (Qwen3.5-4B Q4_K_M, 4 threads on the VPS, temperature 0, one run). **Not a benchmark.** Two changes since: the fixed prompt cut from 2,150 to 2,000 tokens (the same commands in fewer words), and two more example families, of level 2, on the prototype of administration doubles (`tests/doubles/admin.py`): a service that does not start because of a misspelt word in its configuration, and a full disk whose biggest file has to be found and deleted.

### The shorter prompt, on the eighty variants of the entry above

| | 2,150 tokens | 2,000 tokens |
| --- | --- | --- |
| Passed | 74 of 80 | 72 of 80 |
| Difference, variant by variant | | −2.5 points, 95 % interval −10.0 to 0.0 |

The interval includes zero. The two variants lost are both of the family that reads a note: in one the model looked at the screen instead of reading the file, in the other it ran `cat /home/user/meeting.txt`. Neither sentence about files and `look` was touched by the cut. Seventy-eight variants came out the same. Read as: no sign that the cut hurts, and not enough tasks to see a small effect; the cut stays. With the prompt saved between sessions the median variant took 24 s instead of 36.

### The two administration families

| Family | Passed | What the failures were |
| --- | --- | --- |
| A service with a misspelt word in its configuration | 3 of 10 | It finds the cause in the log almost every time. Then: it explains the fix and stops without making it; or it corrects the file and restarts the service without `sudo`, is refused, and does not try with it; or it wanders (the network, the wifi) before looking at the service |
| A full disk | 6 of 10 | Twice it named the folder, not the file. Once it deleted the whole folder that held the big file, and a bill with it. Once it told the user to run `du` themselves |

When it works it is the real procedure: `systemctl status`, `journalctl -u`, `sed -i` on the file, `sudo systemctl restart`, the password typed by the user, `systemctl status` again.

### What it says

1. **Administration tasks can be run without a real system,** at the cost of 60 to 250 s each with the 4B on the VPS. The doubles gave the model what it needed to succeed and to fail in ways a real system would show.
2. **These are the tasks that separate.** Level 1 passes at 90 %; the first two of level 2 pass at 30 % and 60 %. This is where the 4B, the 2B and the 35B should be compared, and where training has room.
3. **A limit of the doubles showed at once:** inside the session every file belongs to the user, so `sed -i /etc/...` works without `sudo`, where a real system refuses. Services do need it. A family can demand `sudo` in the trajectory, as this one does, but a model that forgets it is helped by the pretend system.
4. Deleting a folder to free space, when asked to delete one file, is a new entry for the list of failures worth a family: `unchanged` for everything the user did not name.

## 24 November 2026 — `ask_server` with real models: the 4B hands over to the 35B

**Spent: 0 %.** A trial of the plumbing, not a measurement: four conversations in the benchmark's sessions, the 4B local (4 threads) and the VPS's 35B as its second model, on a service that does not start because of a misspelt word in its configuration. The user asks for the hand-over outright ("pass this to the big model on my server"), so this says nothing about whether the 4B knows when to hand over.

| | First try | After the fix |
| --- | --- | --- |
| The 4B called `ask_server` | 4 of 4 | 4 of 4 |
| The 35B took the task as its own | 0 of 4 | 4 of 4 |
| The service repaired | 0 of 4 | 2 of 4 |
| Time per conversation | 36 to 103 s | 147 to 310 s |

- **First try:** the 35B answered "the task has been handed over to the larger model" or asked the user for details. It was shown the conversation with the small model's `ask_server` call in it, and read it as a task already in other hands.
- **The fix:** the second model gets the conversation without that call, and a note in its system prompt: the small model could not do this, the task is yours, carry it out with the tools.
- **After:** in two conversations the 35B read `systemctl status` and the file, corrected the word, restarted the service (first without `sudo`, refused; then with it) and checked. In the other two it went looking for packages and hardware and used up the twelve steps of a turn. One of the two that worked went on, after the repair, to try to install printing tools.
- **What it says for the plan:** the hand-over works end to end and the benchmark can measure it (`escalated`, and the state afterwards). The untrained 35B does not go straight to the service's log either: the teacher needs its own fine-tuning (phase 4.1) before its trajectories are worth keeping, and only the ones the verifier approves are.

## 24 November 2026 — the 2B against the 4B on the example families

**Spent: 0 %.** Same ten example families and the same hundred variants as the entry of the shorter prompt (level 1 and two families of level 2), same conditions, one run each. **Not a benchmark**; the first time the two candidates are compared variant by variant and with an interval.

| | Qwen3.5-4B Q4_K_M | Qwen3.5-2B Q4_K_M |
| --- | --- | --- |
| All ten families | 81.0 % (66.0 to 93.0) | 58.0 % (36.0 to 79.0) |
| Reading a setting, a "no" | 100 %, 100 % | 100 %, 100 % |
| A change (volume, wifi) | 95 % | 55 % |
| Files | 80 % | 75 % |
| Recalling a name | 80 % | 50 % |
| Read, then set | 90 % | 50 % |
| A full disk (level 2) | 60 % | 20 % |
| A service that does not start (level 2) | 30 % | 0 % |
| Median time per variant | 24 s | 16 s |

**Difference: −23 points for the 2B, 95 % interval −40 to −7.** It leaves zero out.

### What it says

1. **The untrained 2B is clearly behind the untrained 4B**, and now with an interval that says so; on 14 Nov it was 58 against 76 of 81 without one.
2. **It falls where there is more than one step:** joining a wifi (2 of 10: it does not get from the scan to the connect command), read-then-set, the two administration families. Where one command does it, it matches the 4B.
3. **It is not the 4B at two thirds of the wait any more:** with the prompt saved between sessions the difference is 16 s against 24 s per variant on the VPS, because most of the wait is now writing, not reading.
4. This does not settle the choice: the question was never whether the untrained 2B is as good, but whether a fine-tuned 2B reaches a useful level on a laptop where the 4B is too slow. The pilot fine-tunes both. What this adds is the size of the gap to close: 23 points, most of it on tasks of several steps.

## 24 November 2026 — the 35B as the only model on the example families, and the administration families repaired

**Spent: 0 %.** **Not a benchmark.** The base Qwen3.6-35B-A3B Q4_K_M on the VPS's processor as the chat's provider (as after `/pair`: `bench/run.py --provider`), on the same hundred variants as the 4B and the 2B, temperature 0, reasoning off, one run. Then the two administration families run again with the 4B and the 35B, after repairing their scenario.

### The hundred variants

| | 35B | 4B | 2B |
| --- | --- | --- | --- |
| All ten families | 79.0 % (60.0 to 94.0) | 81.0 % (66.0 to 93.0) | 58.0 % (36.0 to 79.0) |
| The eight of level 1 | 90.0 % | 90.0 % | 70.0 % |
| 35B minus 4B, variant by variant | −2.0 points (−14.0 to +9.0) | | |

The untrained 35B is not ahead of the untrained 4B here: the interval includes zero. Read with these in mind:

1. **As a provider it does not get what the user asked to remember** (by design, it is personal data): the family that recalls a name gave 60 %, and the passes came from asking `osyr memory list`.
2. **It miscopies names it has just read:** `MOVISTAR_F405` for the `MOVISTAR_F406` of the scan, three times in a row until the turn was stopped; on 23 Nov, `XM5` for `XM4` and `shopping_list` for `shopping-list`. That server runs with penalties on repetition (`--repeat-penalty 1.1`, DRY), set for another project to stop loops; a penalty on repeating is a penalty on copying a name exactly, which is what a tool call needs. Its configuration is not ours to touch; for OSYR's own server model the penalties should be off or measured.
3. **Two of the ten families were unfair to both models** (see below).

### The administration families had faults of their own

Reading the 35B's failures showed that the scenario, not the task, sent the models the wrong way: the pretend laptop had its wifi disconnected (the 35B set out to fix the network before looking at SSH or the clock), and the failing service had no program on the disk (both models concluded "it is not installed" and went to install it). **Repaired:** the machine is connected and the service's program and unit file exist. Since the first runs `/etc` is also closed to writing without `sudo`.

| Family, after the repair | 4B | 35B |
| --- | --- | --- |
| A service that does not start (misspelt word) | 5 of 10 (was 3) | 5 of 10 (was 3) |
| A full disk | 7 of 10 (was 6) | 5 of 10 (was 4) |
| Median time per variant | 96 s | 78 s |

What is left is the models': the 35B twice answered "delete it" with `rm -rf ~/Downloads/*`, a bill included; both still decide that a clock service "is not installed" when `ps` shows no such process and `timedatectl` does not exist (the doubles have no processes: part of this is still the pretend system's); both stop at the diagnosis in some variants.

### What it says

1. **A family has to be read in its failures before its score means anything.** Two scenario faults moved both models by 20 points on one family. The plan's label for each failure (missing feature, wrong tool, model error) needs a fourth: the task's own fault.
2. **The doubles decide what "administration" measures.** Every tell of a pretend system (no processes, no `timedatectl`, a box that cannot install) becomes a wrong lead for the model. Either the doubles grow until the common leads hold (processes first), or these tasks run on the real image in a virtual machine.
3. **The base 35B on this server is not a teacher yet** on these tasks: about half, and with a destructive answer among them. The plan already fine-tunes it first (phase 4.1) and keeps only verified trajectories; this is the first number behind that.
4. The runner can now run several variants at once (`--jobs`): the administration families took 96 s per variant with two 4B servers side by side.

## 24 November 2026 (later) — processes and the clock in the pretend system, and three more faults of the tasks' own

**Spent: 0 %.** **Not a benchmark.** The administration doubles gained processes and the clock (`ps`, `pgrep`, `top`, `kill`, `timedatectl`; see [MILESTONE-0.md](MILESTONE-0.md)) and a third administration family, a program hogging the processor that has to be found and ended (`runaway-process`). The 4B (local, two servers side by side) and the base 35B (the VPS's, as the only provider), ten variants each, temperature 0, reasoning off, one run.

| Family | 4B | 35B |
| --- | --- | --- |
| A program hogging the processor | 9 of 10 | 7 of 10 |
| A service that does not start (misspelt word), after the repairs below | 8 of 10 (was 5) | 3 of 10 (was 5) |
| Median time per variant, the two families | 101 s and 121 s | 51 s and 89 s |

With ten variants a family's interval is some 50 points wide (the report gives 50 to 100 for the 4B's 8 of 10): none of these differences is established, the 35B's fall from 5 to 3 included.

### The faults that were the tasks'

The first run of the day gave the 4B 3 of 10 on the service family. Reading the failures before using the number, as the morning's entry asks:

1. **The log named a line that was not there.** It said "line 18" (or 7, or 12) of a file of three lines; a model deleted line 18 and went on from there. The log now names the line the word is on.
2. **Commenting the bad line out did not count as a repair.** The service looked for the word anywhere in the file, comments included, and so did the family's own check. Three of the 4B's failures were this. A commented line is no longer a setting, and the family only asks that the service runs.
3. **The verifier judged an answer by its last words.** "It is `ffmpeg`, I will stop it", a command, then "Done": the name was not in the last message. An answer is now everything the agent said in the turn. Two of the 4B's three failures on the new family were this (7 of 10 before, 9 after, the same conversations judged again).
4. Apart from the tasks: `bench/run.py` wrote no report since the sandbox got a `report.py` of its own (it was importing that one); the verdicts were saved all the same.

### What is left is the models'

- **The 35B does not use `sudo` here.** In six of its seven failures on the service family it found the cause, tried the fix without `sudo`, was refused, and told the user to type the `sudo` command in a terminal. The 4B, with the same prompt ("for administrator tasks write sudo in front"), writes it. In the morning's run the 35B did use it in the variants it passed; at temperature 0 a change in what a command prints is enough to move it.
- **Both ask again what was already asked.** "Find what is using the processor and close it": the 35B three times, the 4B once, found the program and asked "shall I stop it?" The system's own question before a command already covers that.
- The 4B once repaired the file and started the program by hand instead of the service; once it gave the user the commands instead of running them.
- With processes to look at and a clock that answers, no conversation ended in "it is not installed" this time.

### What it says

1. **Two families, three faults of their own, in a day.** The fourth label is not a rare case: each new family needs one read of its failures with one model before it counts, and that read is part of writing it. Forty-four families at ten variants, at about 110 s a variant with two servers side by side, would be some seven hours of the 4B on the VPS for that first pass (an estimate from these two families).
2. **Ten variants do not separate two models on one family.** What separates them is the whole set (the plan's intervals over families); a single family's score is for reading its failures.
3. **The 35B and `sudo` is the first thing to look at in its trajectories** before any of them is kept as a teacher's: a teacher that hands the administrator's command back to the user teaches exactly the wrong thing. Not yet tried: the same family with the 35B's repetition penalties off, which needs a server of our own.
4. The 35B measured alone is quick (51 s a variant on the new family); measured while two 4B servers ran on the same sixteen processors its first two variants hit the eleven-minute limit. Runs that are compared must not share the machine.
