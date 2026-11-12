################################################################################
#
# wl-mirror — mirror displays on Wayland
#
################################################################################

WL_MIRROR_VERSION = 0.18.5
WL_MIRROR_SITE = https://github.com/Ferdi265/wl-mirror/releases/download/v$(WL_MIRROR_VERSION)
WL_MIRROR_LICENSE = GPL-3.0+
WL_MIRROR_LICENSE_FILES = LICENSE
WL_MIRROR_DEPENDENCIES = host-pkgconf host-wayland wayland wayland-protocols mesa3d
WL_MIRROR_CONF_OPTS = -DINSTALL_EXAMPLE_SCRIPTS=OFF -DINSTALL_DOCUMENTATION=OFF \
	-DWITH_LIBDECOR=OFF -DWITH_GBM=OFF

$(eval $(cmake-package))
