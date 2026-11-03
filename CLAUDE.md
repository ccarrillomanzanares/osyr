# osyr — context for Claude Code

Common base of OSYR and newos: a Buildroot `BR2_EXTERNAL` tree (x86_64) with a local AI agent that runs on the CPU of 8 GB laptops. Repository: github.com/ccarrillomanzanares/osyr.

- Architecture and decisions: `docs/ARCHITECTURE.md`.
- Work in progress and piece-by-piece status: `docs/MILESTONE-0.md`. Update it when each piece is finished.
- Project language: English everywhere (docs, comments, file names, UI messages and the website).
- Reference repo to consult (do not modify from here): newos (github.com/ccarrillomanzanares/newos). newos already solved BIOS/UEFI boot, a kernel without initramfs and Wayland on Buildroot.
- Before publishing an image: boot it in QEMU. Do not end commands with `| tail -1` without checking the exit code (it hides errors; it happened with xorriso and with git push).
