# osyr — context for Claude Code

OSYR: a Buildroot `BR2_EXTERNAL` tree (x86_64) with a local AI agent that runs on the CPU of ordinary laptops (target: 16 GB with AVX2; see the training plan). Repository: github.com/ccarrillomanzanares/osyr.

- Architecture and decisions: `docs/ARCHITECTURE.md`.
- Work in progress and piece-by-piece status: `docs/MILESTONE-0.md`. Update it when each piece is finished.
- Features, benchmark and fine-tuning plan, with its working rules: `docs/TRAINING-PLAN.md`. Read it at the start of every session.
- Project language: English everywhere (docs, comments, file names, UI messages and the website).
- Before publishing an image: boot it in QEMU. Do not end commands with `| tail -1` without checking the exit code (it hides errors; it happened with xorriso and with git push).
