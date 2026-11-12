################################################################################
#
# mako — desktop notifications
#
################################################################################

MAKO_VERSION = 1.11.0
MAKO_SITE = https://github.com/emersion/mako/releases/download/v$(MAKO_VERSION)
MAKO_LICENSE = MIT
MAKO_LICENSE_FILES = LICENSE
MAKO_DEPENDENCIES = host-pkgconf host-wayland wayland wayland-protocols cairo pango systemd
MAKO_CONF_OPTS = -Dsd-bus-provider=libsystemd -Dicons=disabled -Dman-pages=disabled \
	-Dzsh-completions=false -Dbash-completions=false -Dfish-completions=false

$(eval $(meson-package))
