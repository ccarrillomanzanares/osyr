# OSYR help

Open this help at any time with Super+F1, or type `osyr-help` in a terminal. Super is the key with the Windows logo.

## The screen is locked or black

The screen locks after 20 minutes without use, when you press Super+Ctrl+L, and before the laptop sleeps (lid closed). 5 minutes after locking it also turns off. To change the time: `osyr ui lock 30` (minutes), `osyr ui lock off` (never by itself), or ask the assistant.

- Black screen: press any key or move the mouse.
- Green ring: the screen is locked. Type your password (nothing is shown, the ring lights up with each key) and press Enter.
- Red ring: wrong password. Type it again; check Caps Lock and the keyboard layout.
- If the password is never accepted or the screen stays black: press Ctrl+Alt+F2, log in as `osyr` with your password, type `pkill swaylock` and press Enter, then press Ctrl+Alt+F1 to go back.
- If nothing reacts at all, hold the power button for 5 seconds. Then run `osyr-diag` so we can see what happened.

## The assistant

The OSYR terminal opens when the session starts. Type a message and press Enter, or hold Super+Space and speak.

| In the chat | What it does |
|---|---|
| `/voice` | speak instead of typing (same as holding Super+Space) |
| `/look [question]` | show the screen to the assistant |
| `/speak on`, `/speak off` | spoken answers on or off |
| `/history`, `/resume [n]` | earlier conversations, and continue one |
| `/clear` | start a new conversation |
| `/ai`, `/ai <name> [model]` | list the AI providers, switch to another one |
| `/pair` | add an AI provider by entering its key on your phone |
| `/install` | install OSYR on this computer's disk, from the USB (erases the whole disk) |
| `/update`, `/update undo` | install OSYR updates, or go back to the previous version |
| `/report` | send a problem report to the OSYR team: it asks what happened, then sends logs and hardware data |
| `/report chats` | the same, plus your last conversations with the assistant (this time only): they help us see what went wrong |
| `/help` | short help |
| `/exit` | close the chat (a normal terminal stays open) |

**OSYR is still in development and can make mistakes, and so can its AI.** Check what it says, and what it is going to do, before you confirm. Do not give it sensitive data (bank or health details, other people's personal data) and do not use it for delicate operations (banking, payments, important files). With a remote AI provider, what you write is sent to that provider. OSYR is provided “as is”, without warranty of any kind: you use it at your own risk, and its authors are not liable for any damage or loss of data (MIT License). Keep a backup of your important files (`osyr backup run`).

The assistant asks before changing anything. Ask it things like "connect to my wifi", "turn the volume down", "remind me at 6 to call Ana" or "how hot is the processor?".

It can also find and read your files (it never changes them): "where is the electricity bill PDF?", "summarise the notes in Documents", "translate this file into English". It never reads the folders with your keys and passwords.

It remembers what you ask it to: "remember that my sister is called Ana", "remember that I prefer short answers". It never saves passwords, PINs or card numbers. It only uses what it remembers with the local model, never with a remote provider. To see or erase it: `osyr memory list`, `osyr memory forget <n>` or `osyr memory forget all`.

The first `/look` after a while takes a few more seconds: OSYR loads the part of the model that sees images only when it is needed, and frees that memory after 10 minutes without use.

## Backups

Plug in a USB or external disk and ask the assistant to "make a backup", or run `osyr backup run <disk>` (`osyr disk list` shows the name). It copies your folder to `OSYR-backup` on that disk, in the background, and shows a notice when it finishes; the next time it only copies what changed. Eject the disk before unplugging it.

The copy is made of normal files you can open on any computer, so it is not encrypted, and it never includes the folders with your keys and passwords (AI provider keys, SSH). To bring your files back: `osyr backup restore <disk>`. It does not delete anything and does not overwrite files that are newer on this computer.

## Notifications

Notices appear in the top right corner: reminders, low battery, updates, temperature. They go away after a few seconds or when you click them; reminders and urgent ones (battery almost empty) stay until you click. Missed one? `makoctl restore` shows the last one again.

## Updates

OSYR checks for updates once a day (and 15 minutes after starting) and tells you with a notice. It never installs anything by itself.

- Only versions signed by the OSYR team are offered, and never an older one.
- To install: type `/update` in the OSYR chat. It asks for your password, shows what will change and asks before applying it.
- On a computer where OSYR is installed, a new version arrives whole, without reinstalling: `/update` downloads it (about 4 GB, to your files' disk), writes it next to the current system and starts it on the next restart. Your files, password, wifi and settings stay. If the new version does not start, the computer goes back to the previous one by itself; you can also choose it holding Shift at start-up ("OSYR (previous version)").
- Your own changes to settings files are kept; if one clashes, the new version is saved next to it as `.osyr-new`.
- Something went wrong? `/update undo` goes back to what you had before the last update.
- In a terminal: `sudo osyr update apply` and `undo`; `sudo osyr update image check|apply|rollback` (whole versions) and `sudo osyr update code check|apply|rollback` (code only). Turn the daily check off: `AUTO_CHECK=no` in `/etc/osyr/update.conf`.
- Running from the USB, new versions are not installed: download the new image from ccmai.org/osyr.

## The web browser

The browser is part of the assistant. Ask it "open the weather in Madrid", "search for train times to Valencia" or "read me this page and summarise it": it opens the page, reads it and can follow links. It asks before pressing buttons or filling in forms, and it never types passwords or card numbers: you type those yourself in the browser window.

- Super+O: type an address or what you want to search, and press Enter.
- The pages open on workspace 2 (Super+2), as tabs. Super+1 goes back to the chat.
- There is no address bar: use Super+O, or ask the assistant.
- Sites that need DRM (Netflix, Spotify, Disney+) do not play.

## Keyboard shortcuts

| Keys | What it does |
|---|---|
| Super+F1 | this help |
| Super+C | open the OSYR assistant |
| Super+Enter | open a terminal |
| Super+D | run a program by name |
| Super+O | open a web address or search the web |
| Hold Super+Space | talk to the assistant |
| Hold Super+Ctrl+Space | dictate into the window you are using |
| Super+Ctrl+L | lock the screen |
| Print Screen | screenshot (saved in Pictures/Screenshots) |
| Super+Shift+Q | close the window |
| Super+F | full screen on or off |
| Super+Arrows (or H J K L) | move between windows |
| Super+Shift+Arrows (or H J K L) | move the window |
| Super+1 to Super+5 | go to workspace 1 to 5 |
| Super+Shift+1 to 5 | send the window to workspace 1 to 5 |
| Super+B, Super+V | next window beside, or below |
| Super+W, Super+S, Super+E | tabs, stacked, side by side |
| Super+Shift+Space | floating window on or off |
| Super+Shift+C | reload the desktop settings |
| Super+Shift+E | leave the desktop (type `exec osyr-session` to start it again) |
| Brightness, volume and mute keys | work as usual, also on the lock screen |

## Useful commands

Most of these you can simply ask the assistant for, in your own words. To type them yourself, open a terminal (Super+Enter); the ones with `sudo` ask for your password.

| Command | What it does |
|---|---|
| `osyr net status` | network and Internet |
| `osyr net wifi scan`, `osyr net wifi connect <name>` | wifi networks |
| `osyr audio status`, `osyr audio test` | sound |
| `osyr bt status`, `osyr bt scan`, `osyr bt connect <device>` | Bluetooth |
| `osyr ui display list` | screens; also `extend`, `mirror`, `on`, `off` |
| `osyr ui lock [minutes\|off]` | when the screen locks by itself |
| `osyr sys selftest` | test the hardware |
| `osyr-hwtest` | guided test of this computer (about 10 minutes: model, vision, browser, USB, battery...; asks a few questions) and send the result |
| `osyr sys thermal`, `osyr sys power` | temperature and battery |
| `osyr remind list` | reminders |
| `osyr web open <address or words>`, `osyr web read` | browser: open a page or search, read it |
| `osyr files find <words>`, `osyr files read <file>` | find and read your files |
| `osyr disk list`, `osyr disk eject <name>` | USB sticks and external disks: they open by themselves when plugged in; eject before unplugging |
| `osyr memory list`, `osyr memory forget <n\|all>` | what the assistant remembers about you |
| `osyr backup run <disk>`, `osyr backup status`, `osyr backup restore <disk>` | copy your folder to a USB or external disk, and bring it back |
| `osyr doc <words>` | this help, by topic |
| `sudo osyr update code check` | look for OSYR updates (see Updates above) |
| `sudo osyr-install` | install OSYR on the disk (erases the whole disk; same as `/install` in the chat) |
| `osyr-diag` | send a problem report: logs and hardware (same as `/report` in the chat). Your conversations go only with `--chats`, or always if you put `SEND_CHATS=yes` in `~/.config/osyr/diag.conf` |

## Credits

OSYR (MIT License) is built with Buildroot, llama.cpp and the Qwen3.5 model, whisper.cpp for listening, and sherpa-onnx (Apache-2.0) with Piper voices for speaking. The voices and their licences: Spanish "sharvard" (CC BY 3.0), English "libritts_r" (CC BY 4.0), Catalan "upc_ona" (CC BY-SA 3.0 ES, UPC), French "siwis" (CC BY 4.0), German "thorsten" (CC0), Italian "riccardo" (M-AILABS) and Portuguese "faber" (CC0). Each voice's details are in /usr/share/osyr/models/piper/<language>/MODEL_CARD. For other languages OSYR speaks with eSpeak.
