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
