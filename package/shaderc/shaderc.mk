################################################################################
#
# shaderc — glslc, the GLSL to SPIR-V compiler (host only)
#
# llama.cpp compiles its Vulkan shaders with glslc while it is built, and
# Buildroot has no package for it. shaderc builds against its own copies of
# glslang, SPIRV-Tools and SPIRV-Headers, at the revisions its DEPS file names
# for this version: they are downloaded here and unpacked into third_party/.
#
################################################################################

SHADERC_VERSION = 2026.1
SHADERC_SOURCE = shaderc-$(SHADERC_VERSION).tar.gz
SHADERC_SITE = https://github.com/google/shaderc/archive/v$(SHADERC_VERSION)
SHADERC_LICENSE = Apache-2.0
SHADERC_LICENSE_FILES = LICENSE

SHADERC_GLSLANG = f0bd0257c308b9a26562c1a30c4748a0219cc951
SHADERC_SPIRV_HEADERS = 04f10f650d514df88b76d25e83db360142c7b174
SHADERC_SPIRV_TOOLS = fbe4f3ad913c44fe8700545f8ffe35d1382b7093
HOST_SHADERC_EXTRA_DOWNLOADS = \
	https://github.com/KhronosGroup/glslang/archive/$(SHADERC_GLSLANG)/glslang-$(SHADERC_GLSLANG).tar.gz \
	https://github.com/KhronosGroup/SPIRV-Headers/archive/$(SHADERC_SPIRV_HEADERS)/spirv-headers-$(SHADERC_SPIRV_HEADERS).tar.gz \
	https://github.com/KhronosGroup/SPIRV-Tools/archive/$(SHADERC_SPIRV_TOOLS)/spirv-tools-$(SHADERC_SPIRV_TOOLS).tar.gz

define HOST_SHADERC_UNPACK_THIRD_PARTY
	mkdir -p $(@D)/third_party/glslang $(@D)/third_party/spirv-headers $(@D)/third_party/spirv-tools
	tar -xzf $(HOST_SHADERC_DL_DIR)/glslang-$(SHADERC_GLSLANG).tar.gz --strip-components=1 -C $(@D)/third_party/glslang
	tar -xzf $(HOST_SHADERC_DL_DIR)/spirv-headers-$(SHADERC_SPIRV_HEADERS).tar.gz --strip-components=1 -C $(@D)/third_party/spirv-headers
	tar -xzf $(HOST_SHADERC_DL_DIR)/spirv-tools-$(SHADERC_SPIRV_TOOLS).tar.gz --strip-components=1 -C $(@D)/third_party/spirv-tools
endef
HOST_SHADERC_POST_EXTRACT_HOOKS += HOST_SHADERC_UNPACK_THIRD_PARTY

HOST_SHADERC_CONF_OPTS = \
	-DSHADERC_SKIP_TESTS=ON \
	-DSHADERC_SKIP_EXAMPLES=ON \
	-DSHADERC_SKIP_COPYRIGHT_CHECK=ON \
	-DSPIRV_SKIP_TESTS=ON \
	-DSPIRV_SKIP_EXECUTABLES=ON \
	-DENABLE_GLSLANG_BINARIES=OFF \
	-DSPIRV_HEADERS_SKIP_EXAMPLES=ON

$(eval $(host-cmake-package))
