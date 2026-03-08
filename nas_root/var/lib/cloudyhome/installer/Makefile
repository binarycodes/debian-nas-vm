.PHONY: install packages pull-images pip-install permissions validate-install zedlet-symlinks systemd-setup

SBIN     := /usr/local/sbin
LIB      := /usr/local/lib/cloudyhome
VAR      := /var/lib/cloudyhome
UNITDIR  := /etc/systemd/system
ZEDDIR   := /etc/zfs/zed.d
SERVICES := $(VAR)/nas/services.yml

install: packages pull-images pip-install permissions validate-install systemd-setup zedlet-symlinks

# Install all required system packages.
# sops and age are pre-installed in the base golden image and are not listed here.
# zfs-zed is pulled in automatically as a dependency of zfsutils-linux on Debian.
packages:
	DEBIAN_FRONTEND=noninteractive apt-get install -y \
		zfsutils-linux \
		nfs-kernel-server \
		samba \
		targetcli-fb \
		podman \
		cloud-init \
		nftables \
		yq \
		smartmontools \
		msmtp \
		python3 \
		python3-pip

# Pre-pull container images so Quadlet services can start at boot without network access.
# Image names are read from services.yml (.garage.image, .ftp.image) so the Makefile
# never needs updating when versions change.
pull-images:
	for image in $$(yq -r '[.garage.image, .ftp.image] | .[] | select(. != null)' $(SERVICES)); do \
		podman pull "$$image"; \
	done

# Install the cloudyhome Python library system-wide.
# --break-system-packages is required on Debian 12+ when installing outside a venv.
pip-install:
	pip3 install --break-system-packages $(LIB)

# Set correct permissions on all installed files.
permissions:
	chmod 0755 \
		$(SBIN)/nas-apply-config \
		$(SBIN)/nas-garage-bootstrap \
		$(SBIN)/nas-health-alert \
		$(SBIN)/nas-render-config \
		$(SBIN)/nas-validate-config \
		$(SBIN)/nas-validate-install-phase \
		$(SBIN)/nas-zedlet-wrapper \
		$(SBIN)/nas-zfs-import
	chmod 0600 $(VAR)/nas/secrets.enc.yaml
	chown root:root $(VAR)/nas/secrets.enc.yaml

validate-install:
	nas-validate-install-phase

# Post-copy systemd configuration.
# daemon-reload picks up the unit files copied from nas_root.
# enable creates the WantedBy symlinks for the cloudyhome boot chain.
# Mask/disable prevents stock services from racing with the cloudyhome boot chain.
systemd-setup:
	systemctl daemon-reload
	systemctl enable \
		cloudyhome-nas-validate.service \
		cloudyhome-zfs-import.service \
		cloudyhome-nas-render.service \
		cloudyhome-nas-firewall.service \
		cloudyhome-nas-apply.service \
		cloudyhome-zfs-scrub.timer
	systemctl mask \
		zfs-import-cache.service \
		zfs-import-scan.service \
		zfs-mount.service \
		zfs-share.service
	systemctl disable nfs-server smbd target

# ZED event handler symlinks (not systemd units, so managed manually).
zedlet-symlinks:
	ln -sf $(SBIN)/nas-zedlet-wrapper  $(ZEDDIR)/statechange-nas-health-alert.sh
	ln -sf $(SBIN)/nas-zedlet-wrapper  $(ZEDDIR)/scrub_finish-nas-health-alert.sh
	ln -sf $(SBIN)/nas-zedlet-wrapper  $(ZEDDIR)/resilver_finish-nas-health-alert.sh
	ln -sf $(SBIN)/nas-zedlet-wrapper  $(ZEDDIR)/io-nas-health-alert.sh
