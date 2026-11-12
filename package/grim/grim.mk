################################################################################
#
# grim — screenshots for Wayland
#
################################################################################

GRIM_VERSION = 1.4.0
GRIM_SOURCE = v$(GRIM_VERSION).tar.gz
GRIM_SITE = https://github.com/emersion/grim/archive/refs/tags
GRIM_LICENSE = MIT
GRIM_LICENSE_FILES = LICENSE
GRIM_DEPENDENCIES = host-pkgconf host-wayland wayland wayland-protocols pixman libpng
GRIM_CONF_OPTS = -Djpeg=disabled -Dman-pages=disabled

$(eval $(meson-package))
