################################################################################
#
# swaylock — screen lock
#
################################################################################

SWAYLOCK_VERSION = 1.8.6
SWAYLOCK_SITE = https://github.com/swaywm/swaylock/releases/download/v$(SWAYLOCK_VERSION)
SWAYLOCK_LICENSE = MIT
SWAYLOCK_LICENSE_FILES = LICENSE
SWAYLOCK_DEPENDENCIES = host-pkgconf host-wayland wayland wayland-protocols cairo \
	libxkbcommon linux-pam
SWAYLOCK_CONF_OPTS = -Dpam=enabled -Dgdk-pixbuf=disabled -Dman-pages=disabled \
	-Dzsh-completions=false -Dbash-completions=false -Dfish-completions=false

$(eval $(meson-package))
