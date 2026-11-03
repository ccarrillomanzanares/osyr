# OSYR — build shortcuts on top of Buildroot.
#   make            configures (the first time) and builds the image
#   make model      downloads Qwen3.5-4B to models/ (before 'make')
#   make qemu       boots the image in QEMU (BIOS); 'make qemu-uefi' with OVMF
#   make test       tests: the image's binaries here + the image in QEMU
#                   + the disk installer (test-native, test-vm, test-install)
#                   (OSYR_TEST_SMP=N: VM CPUs, 12 by default)
#   make menuconfig / savedefconfig / linux-menuconfig
#   make clean      deletes build/output (not the Buildroot download)

BR_VERSION  := 2026.08.1
BR_DIR      := $(CURDIR)/build/buildroot
OUT_DIR     := $(CURDIR)/build/output
DEFCONFIG   := osyr_x86_64_defconfig
IMAGE       := $(OUT_DIR)/images/osyr.img
# All cores: with BR2_PER_PACKAGE_DIRECTORIES Buildroot builds several
# packages at once and shares the job server (-j) among them.
JOBS        ?= $(shell nproc)
BR          := $(MAKE) -C $(BR_DIR) O=$(OUT_DIR) BR2_EXTERNAL=$(CURDIR)

# QEMU: memory of the minimum profile; QEMU_DISPLAY=vnc=:1 for a headless server
QEMU_MEM    ?= 8G
QEMU_SMP    ?= 4
QEMU_DISPLAY ?= gtk
OVMF_CODE   ?= $(firstword $(wildcard /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd))
OVMF_VARS   ?= $(firstword $(wildcard /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd /usr/share/edk2/x64/OVMF_VARS.4m.fd))
KVM         := $(if $(wildcard /dev/kvm),-enable-kvm -cpu host,-cpu max)
QEMU        := qemu-system-x86_64 $(KVM) -m $(QEMU_MEM) -smp $(QEMU_SMP) \
	-drive file=$(IMAGE),format=raw,if=virtio \
	-device virtio-vga -display $(QEMU_DISPLAY) \
	-nic user,model=virtio-net-pci,hostfwd=tcp::2222-:22 \
	-serial mon:stdio

.PHONY: all model qemu qemu-uefi menuconfig savedefconfig linux-menuconfig clean test test-native test-vm test-install

all: $(OUT_DIR)/.config
	$(BR) -j$(JOBS)

$(BR_DIR)/Makefile:
	git clone --depth 1 -b $(BR_VERSION) https://gitlab.com/buildroot.org/buildroot.git $(BR_DIR)

# Also when the defconfig changes: otherwise a package enabled in configs/ did
# not reach the image (happened with sherpa-onnx)
$(OUT_DIR)/.config: $(BR_DIR)/Makefile configs/$(DEFCONFIG)
	$(BR) $(DEFCONFIG)

model:
	scripts/download-model.sh

qemu:
	@test -f $(IMAGE) || { echo "No image: run 'make' first"; exit 1; }
	$(QEMU)

qemu-uefi:
	@test -f $(IMAGE) || { echo "No image: run 'make' first"; exit 1; }
	@test -n "$(OVMF_CODE)" || { echo "OVMF not found (apt install ovmf)"; exit 1; }
	cp -n $(OVMF_VARS) $(OUT_DIR)/images/OVMF_VARS.fd
	$(QEMU) -drive if=pflash,format=raw,readonly=on,file=$(OVMF_CODE) \
		-drive if=pflash,format=raw,file=$(OUT_DIR)/images/OVMF_VARS.fd

menuconfig savedefconfig linux-menuconfig: $(OUT_DIR)/.config
	$(BR) $@

# Image tests: real binaries on this machine (native, fast) and the image in
# QEMU (vm). Results in build/test-results/.
test test-native test-vm test-install:
	@test -f $(IMAGE) || { echo "No image: run 'make' first"; exit 1; }
	python3 tests/run.py $(if $(filter test,$@),all,$(subst test-,,$@))

clean:
	rm -rf $(OUT_DIR)
