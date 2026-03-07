"""Tests to verify the source tree completeness against MVP1 deliverables."""
import os

import pytest

NAS_ROOT = os.path.join(os.path.dirname(__file__), "..", "nas_root")


class TestSourceTreeCompleteness:
    """Verify all files listed in the deliverables exist in the source tree."""

    EXPECTED_SCRIPTS = [
        "usr/local/sbin/nas-validate-config",
        "usr/local/sbin/nas-render-config",
        "usr/local/sbin/nas-apply-config",
        "usr/local/sbin/nas-zfs-import",
        "usr/local/sbin/nas-garage-bootstrap",
        "usr/local/sbin/nas-health-alert",
        "usr/local/sbin/nas-zedlet-wrapper",
    ]

    EXPECTED_UNITS = [
        "etc/systemd/system/cloudyhome-nas-validate.service",
        "etc/systemd/system/cloudyhome-zfs-import.service",
        "etc/systemd/system/cloudyhome-nas-render.service",
        "etc/systemd/system/cloudyhome-nas-firewall.service",
        "etc/systemd/system/cloudyhome-nas-apply.service",
        "etc/systemd/system/cloudyhome-garage-bootstrap.service",
        "etc/systemd/system/cloudyhome-zfs-scrub.service",
        "etc/systemd/system/cloudyhome-zfs-scrub.timer",
    ]

    EXPECTED_TEMPLATES = [
        "etc/cloudyhome/templates/nftables.conf.j2",
        "etc/cloudyhome/templates/exports.j2",
        "etc/cloudyhome/templates/smb.conf.j2",
        "etc/cloudyhome/templates/saveconfig.json.j2",
        "etc/cloudyhome/templates/garage.toml.j2",
        "etc/cloudyhome/templates/cloudyhome-garage.container.j2",
        "etc/cloudyhome/templates/ftp.env.j2",
        "etc/cloudyhome/templates/cloudyhome-ftp.container.j2",
        "etc/cloudyhome/templates/alert.conf.j2",
        "etc/cloudyhome/templates/msmtprc.j2",
        "etc/cloudyhome/templates/nas-apply-services.sh.j2",
    ]

    EXPECTED_STATIC = [
        "etc/smartd.conf",
        "etc/zfs/zed.d/zed.rc",
    ]

    EXPECTED_DATA = [
        "var/lib/cloudyhome/nas/services.yml",
        "var/lib/cloudyhome/nas/secrets.example.yaml",
    ]

    EXPECTED_LIB = [
        "usr/local/lib/cloudyhome/cloudyhome/__init__.py",
        "usr/local/lib/cloudyhome/cloudyhome/models.py",
        "usr/local/lib/cloudyhome/cloudyhome/secrets.py",
        "usr/local/lib/cloudyhome/cloudyhome/render.py",
        "usr/local/lib/cloudyhome/cloudyhome/validate.py",
    ]

    ALL_FILES = (
        EXPECTED_SCRIPTS + EXPECTED_UNITS + EXPECTED_TEMPLATES +
        EXPECTED_STATIC + EXPECTED_DATA + EXPECTED_LIB
    )

    @pytest.mark.parametrize("rel_path", ALL_FILES)
    def test_file_exists(self, rel_path):
        full = os.path.join(NAS_ROOT, rel_path)
        assert os.path.isfile(full), f"Missing: {rel_path}"

    @pytest.mark.parametrize("script", EXPECTED_SCRIPTS)
    def test_scripts_have_shebang(self, script):
        full = os.path.join(NAS_ROOT, script)
        with open(full) as f:
            first_line = f.readline()
        assert first_line.startswith("#!"), f"Missing shebang: {script}"


class TestStockServicesEnabledByApply:
    """Stock services (smartd, zfs-zed) are enabled at runtime by nas-apply-config, not Packer."""

    def test_apply_enables_smartd(self):
        path = os.path.join(NAS_ROOT, "usr", "local", "sbin", "nas-apply-config")
        content = open(path).read()
        assert "smartd.service" in content

    def test_apply_enables_zfs_zed(self):
        path = os.path.join(NAS_ROOT, "usr", "local", "sbin", "nas-apply-config")
        content = open(path).read()
        assert "zfs-zed.service" in content
