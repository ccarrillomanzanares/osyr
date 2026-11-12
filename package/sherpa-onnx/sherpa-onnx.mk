################################################################################
#
# sherpa-onnx — local neural voice (Piper) for reading aloud
#
################################################################################

SHERPA_ONNX_VERSION = 1.13.8
SHERPA_ONNX_SOURCE = sherpa-onnx-v$(SHERPA_ONNX_VERSION)-linux-x64-shared.tar.bz2
SHERPA_ONNX_SITE = https://github.com/k2-fsa/sherpa-onnx/releases/download/v$(SHERPA_ONNX_VERSION)
# sherpa-onnx: Apache-2.0; onnxruntime: MIT; includes espeak-ng (GPL-3.0) for phonemes
SHERPA_ONNX_LICENSE = Apache-2.0, MIT (onnxruntime), GPL-3.0+ (espeak-ng)
# No LICENSE_FILES: the GitHub binary package does not ship the texts (only bin/,
# include/ and lib/); 'make legal-info' warns about it. The texts are in the
# sherpa-onnx and onnxruntime repositories.

# Only the synthesis program and the library it needs (its RPATH is already
# $ORIGIN/../lib); the other programs (recognition, servers) are not used.
define SHERPA_ONNX_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/bin/sherpa-onnx-offline-tts \
		$(TARGET_DIR)/opt/sherpa-onnx/bin/sherpa-onnx-offline-tts
	$(INSTALL) -D -m 0644 $(@D)/lib/libonnxruntime.so \
		$(TARGET_DIR)/opt/sherpa-onnx/lib/libonnxruntime.so
endef

$(eval $(generic-package))
