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


class TestWantedBySymlinks:
    """Verify multi-user.target.wants/ and timers.target.wants/ symlinks exist with correct targets."""

    SYSTEMD_DIR = os.path.join(NAS_ROOT, "etc", "systemd", "system")

    @pytest.mark.parametrize("unit", [
        "cloudyhome-nas-validate.service",
        "cloudyhome-zfs-import.service",
        "cloudyhome-nas-render.service",
        "cloudyhome-nas-firewall.service",
        "cloudyhome-nas-apply.service",
    ])
    def test_multi_user_wants_symlink(self, unit):
        link = os.path.join(self.SYSTEMD_DIR, "multi-user.target.wants", unit)
        assert os.path.islink(link), f"Missing symlink: {link}"
        target = os.readlink(link)
        assert target == f"../{unit}", f"Wrong target for {unit}: {target}"

    def test_timers_wants_scrub_timer(self):
        link = os.path.join(self.SYSTEMD_DIR, "timers.target.wants", "cloudyhome-zfs-scrub.timer")
        assert os.path.islink(link), f"Missing symlink: {link}"
        assert os.readlink(link) == "../cloudyhome-zfs-scrub.timer"

    def test_garage_bootstrap_not_in_multi_user_wants(self):
        link = os.path.join(self.SYSTEMD_DIR, "multi-user.target.wants", "cloudyhome-garage-bootstrap.service")
        assert not os.path.exists(link), "garage-bootstrap should NOT be in multi-user.target.wants"


class TestZedletSymlinks:
    """Verify ZEDLET symlinks point to nas-zedlet-wrapper."""

    ZED_DIR = os.path.join(NAS_ROOT, "etc", "zfs", "zed.d")
    WRAPPER = "/usr/local/sbin/nas-zedlet-wrapper"

    @pytest.mark.parametrize("zedlet", [
        "statechange-nas-health-alert.sh",
        "scrub_finish-nas-health-alert.sh",
        "io-nas-health-alert.sh",
        "resilver_finish-nas-health-alert.sh",
    ])
    def test_zedlet_symlink_target(self, zedlet):
        link = os.path.join(self.ZED_DIR, zedlet)
        assert os.path.islink(link), f"Missing ZEDLET symlink: {zedlet}"
        assert os.readlink(link) == self.WRAPPER, f"Wrong target for {zedlet}"


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
