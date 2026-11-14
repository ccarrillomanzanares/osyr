################################################################################
#
# OSYR — tweaks to Buildroot packages
#
# Buildroot includes this file AFTER package/*/*.mk, and the cmake options
# (<PKG>_CONF_OPTS) are expanded when the recipe runs, so what is added here
# does reach the build without copying the package.
#
################################################################################

include $(sort $(wildcard $(BR2_EXTERNAL_OSYR_PATH)/package/*/*.mk))

# llama.cpp with ALL x86 CPU variants in a single image.
# The system is built for baseline x86-64 (runs on any 64-bit PC) and ggml
# picks at runtime the fastest variant the CPU supports: AVX1 on an old
# AMD A8, AVX-512 on an i5-1035G1. Without this we would have to choose
# between slow everywhere or SIGILL on old CPUs.
# The backend libraries (libggml-cpu-*.so) are installed next to the
# binaries; GGML_BACKEND_DIR sets where llama-server looks for them.
LLAMA_CPP_CONF_OPTS += \
	-DGGML_NATIVE=OFF \
	-DGGML_BACKEND_DL=ON \
	-DGGML_CPU_ALL_VARIANTS=ON \
	-DGGML_BACKEND_DIR=/usr/lib/ggml

# The Vulkan backend (libggml-vulkan.so, next to the CPU ones) compiles its
# shaders with glslc at build time: OSYR's host-shaderc provides it.
ifeq ($(BR2_PACKAGE_LLAMA_CPP_VULKAN),y)
LLAMA_CPP_DEPENDENCIES += host-shaderc
# The package's rules were already generated when this file is read: the line
# above only reaches what is expanded later (the per-package copy), so the
# order has to be given by hand or a build from scratch never builds glslc.
$(LLAMA_CPP_TARGET_CONFIGURE): | host-shaderc
llama-cpp-depends: host-shaderc
LLAMA_CPP_CONF_OPTS += -DVulkan_GLSLC_EXECUTABLE=$(HOST_DIR)/bin/glslc
endif
