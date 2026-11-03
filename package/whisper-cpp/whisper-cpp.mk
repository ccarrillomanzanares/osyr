################################################################################
#
# whisper-cpp — local speech recognition
#
################################################################################

WHISPER_CPP_VERSION = 1.9.5
WHISPER_CPP_SOURCE = v$(WHISPER_CPP_VERSION).tar.gz
WHISPER_CPP_SITE = https://github.com/ggml-org/whisper.cpp/archive/refs/tags
WHISPER_CPP_LICENSE = MIT
WHISPER_CPP_LICENSE_FILES = LICENSE

# Like llama.cpp: baseline x86-64 and ggml picks the CPU variant at startup
# (AVX1 ... AVX-512), so a single image is fast everywhere without SIGILL.
# Separate prefix: its ggml (0.26) is not llama.cpp's (0.9.x).
WHISPER_CPP_CONF_OPTS = \
	-DCMAKE_INSTALL_PREFIX=/opt/whisper \
	-DCMAKE_INSTALL_RPATH=/opt/whisper/lib \
	-DBUILD_SHARED_LIBS=ON \
	-DGGML_NATIVE=OFF \
	-DGGML_BACKEND_DL=ON \
	-DGGML_CPU_ALL_VARIANTS=ON \
	-DGGML_BACKEND_DIR=/opt/whisper/lib \
	-DGGML_OPENMP=OFF \
	-DWHISPER_BUILD_TESTS=OFF \
	-DWHISPER_BUILD_EXAMPLES=ON \
	-DWHISPER_BUILD_SERVER=OFF \
	-DWHISPER_SDL2=OFF \
	-DWHISPER_CURL=OFF

# Only whisper-cli and its libraries; the CPU variants next to them
define WHISPER_CPP_TRIM_INSTALL
	rm -rf $(TARGET_DIR)/opt/whisper/include $(TARGET_DIR)/opt/whisper/lib/cmake \
		$(TARGET_DIR)/opt/whisper/lib/pkgconfig
	find $(TARGET_DIR)/opt/whisper/bin -type f ! -name whisper-cli -delete
	find $(TARGET_DIR)/opt/whisper -name 'libggml-cpu*.so' ! -path '*/lib/*' \
		-exec mv -f {} $(TARGET_DIR)/opt/whisper/lib/ \;
endef
WHISPER_CPP_POST_INSTALL_TARGET_HOOKS += WHISPER_CPP_TRIM_INSTALL

$(eval $(cmake-package))
