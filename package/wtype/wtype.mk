################################################################################
#
# wtype — types text into the active Wayland window (virtual keyboard)
#
################################################################################

WTYPE_VERSION = 0.5
WTYPE_SOURCE = v$(WTYPE_VERSION).tar.gz
WTYPE_SITE = https://github.com/atx/wtype/archive/refs/tags
WTYPE_LICENSE = MIT
WTYPE_LICENSE_FILES = LICENSE
WTYPE_DEPENDENCIES = host-pkgconf host-wayland wayland libxkbcommon

$(eval $(meson-package))
