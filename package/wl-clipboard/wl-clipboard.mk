################################################################################
#
# wl-clipboard — Wayland clipboard from the command line (wl-copy, wl-paste)
#
################################################################################

WL_CLIPBOARD_VERSION = 2.3.0
WL_CLIPBOARD_SOURCE = v$(WL_CLIPBOARD_VERSION).tar.gz
WL_CLIPBOARD_SITE = https://github.com/bugaevc/wl-clipboard/archive/refs/tags
WL_CLIPBOARD_LICENSE = GPL-3.0+
WL_CLIPBOARD_LICENSE_FILES = COPYING
WL_CLIPBOARD_DEPENDENCIES = host-pkgconf host-wayland wayland wayland-protocols
WL_CLIPBOARD_CONF_OPTS = -Dzshcompletiondir=no -Dfishcompletiondir=no

$(eval $(meson-package))
