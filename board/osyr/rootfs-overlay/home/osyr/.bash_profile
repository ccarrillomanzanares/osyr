# OSYR: logging in on tty1 (autologin) opens the graphical session.
# On other consoles, over SSH or inside foot, it is a normal shell.
[ -f ~/.bashrc ] && . ~/.bashrc
if [ -z "$WAYLAND_DISPLAY" ] && [ "$(tty)" = /dev/tty1 ]; then
    exec /usr/bin/osyr-session
fi
