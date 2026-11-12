################################################################################
#
# noto-emoji — colour emoji font (fontconfig fallback for emoji)
#
################################################################################

NOTO_EMOJI_VERSION = v2026-09-24-unicode18_0
NOTO_EMOJI_SITE = https://raw.githubusercontent.com/googlefonts/noto-emoji/$(NOTO_EMOJI_VERSION)/2D/fonts
# Single files, not a tarball: their names do not carry the version, so on a
# version bump delete dl/noto-emoji/ (the hash check would refuse the old file)
NOTO_EMOJI_SOURCE = NotoColorEmoji.ttf
NOTO_EMOJI_EXTRA_DOWNLOADS = \
	https://raw.githubusercontent.com/googlefonts/noto-emoji/$(NOTO_EMOJI_VERSION)/LICENSE
NOTO_EMOJI_LICENSE = OFL-1.1
NOTO_EMOJI_LICENSE_FILES = LICENSE
NOTO_EMOJI_DEPENDENCIES = libpng

define NOTO_EMOJI_EXTRACT_CMDS
	cp $(NOTO_EMOJI_DL_DIR)/NotoColorEmoji.ttf $(NOTO_EMOJI_DL_DIR)/LICENSE $(@D)/
endef

define NOTO_EMOJI_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/NotoColorEmoji.ttf \
		$(TARGET_DIR)/usr/share/fonts/noto-emoji/NotoColorEmoji.ttf
endef

$(eval $(generic-package))
