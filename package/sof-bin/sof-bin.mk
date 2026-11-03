################################################################################
#
# sof-bin — Intel audio firmware (SOF)
#
################################################################################

SOF_BIN_VERSION = 2026.09.2
SOF_BIN_SITE = https://github.com/thesofproject/sof-bin/releases/download/v$(SOF_BIN_VERSION)
SOF_BIN_LICENSE = BSD-3-Clause (Intel), NXP firmware licence
SOF_BIN_LICENSE_FILES = LICENCE.Intel LICENCE.NXP

# Same as its install.sh without a version suffix: each directory as is
# in /lib/firmware/intel
define SOF_BIN_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/lib/firmware/intel
	for d in sof sof-tplg sof-ipc4 sof-ipc4-tplg sof-ipc4-lib sof-ace-tplg; do \
		if [ -d $(@D)/$$d ]; then \
			rm -rf $(TARGET_DIR)/lib/firmware/intel/$$d; \
			cp -a $(@D)/$$d $(TARGET_DIR)/lib/firmware/intel/$$d || exit 1; \
		fi; \
	done
endef

$(eval $(generic-package))
