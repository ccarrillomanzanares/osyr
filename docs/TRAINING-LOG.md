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
| "It is written in my file nota.txt": the model calls `look` (the screen) first, up to five times, and runs out of its six calls | 10 of 12 runs | Tool descriptions: nothing tells `look` apart from reading a file |
| `files read nota.txt` fails because the file is in `Documents`; it takes a `find` and a second `read` | Most runs | CLI: a bare name could be searched for |
| `files find factura de la luz` finds nothing for `factura-luz-marzo.txt`: every word must match, "de" and "la" included, and a quoted phrase is one word | 10 of 12 runs | CLI: matching too strict |
| After that, the 4B follows the hint (`--content`) and finds it; the 9B stops and asks the user | 9B: 5 of 6 runs fail | Model, and the reason for the 9B's lower score |
| The user says no, and the model asks again for the same command | 4B Q8: 2 of 3 runs | Model |
| A read followed by a change: only the read is done | 4B Q4: 1 of 3 runs | Model |

The first three are worth fixing in the product before any training: they cost nothing and no fine-tuning should spend its capacity learning around them.

### Next

- No choice between 4B and 9B yet. Measure again when the shell and multi-step tasks exist, with the real task format, temperature 0 and the 35B as a reference.
- Until then the default stays the 4B Q4_K_M.
- Proposed fixes, not done: `files find` ignoring short words and splitting quoted phrases; `files read` looking a bare name up; one line in the `look` description ("only for what is on the screen, not for files").
