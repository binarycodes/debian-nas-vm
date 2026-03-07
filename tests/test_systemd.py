"""Tests for systemd unit files - ordering, dependencies, and correctness."""
import os
import re
import configparser

import pytest

SYSTEMD_DIR = os.path.join(os.path.dirname(__file__), "..", "nas_root", "etc", "systemd", "system")


def load_unit(name):
    path = os.path.join(SYSTEMD_DIR, name)
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path)
    return cp


class TestServiceOrdering:
    """Verify the systemd dependency chain matches the plan."""

    def test_validate_after_cloud_init(self):
        u = load_unit("cloudyhome-nas-validate.service")
        assert "cloud-init.target" in u["Unit"]["After"]
        assert "cloud-init.target" in u["Unit"]["Requires"]

    def test_zfs_import_after_validate(self):
        u = load_unit("cloudyhome-zfs-import.service")
        assert "cloudyhome-nas-validate.service" in u["Unit"]["After"]
        assert "cloudyhome-nas-validate.service" in u["Unit"]["Requires"]

    def test_render_after_validate_and_zfs(self):
        u = load_unit("cloudyhome-nas-render.service")
        after = u["Unit"]["After"]
        requires = u["Unit"]["Requires"]
        assert "cloudyhome-nas-validate.service" in after
        assert "cloudyhome-zfs-import.service" in after
        assert "cloudyhome-nas-validate.service" in requires
        assert "cloudyhome-zfs-import.service" in requires

    def test_firewall_after_render(self):
        u = load_unit("cloudyhome-nas-firewall.service")
        assert "cloudyhome-nas-render.service" in u["Unit"]["After"]
        assert "cloudyhome-nas-render.service" in u["Unit"]["Requires"]

    def test_apply_after_render_firewall_zfs(self):
        u = load_unit("cloudyhome-nas-apply.service")
        after = u["Unit"]["After"]
        requires = u["Unit"]["Requires"]
        assert "cloudyhome-nas-render.service" in after
        assert "cloudyhome-nas-firewall.service" in after
        assert "cloudyhome-zfs-import.service" in after
        assert "cloudyhome-nas-render.service" in requires
        assert "cloudyhome-nas-firewall.service" in requires
        assert "cloudyhome-zfs-import.service" in requires

    def test_garage_bootstrap_after_garage(self):
        u = load_unit("cloudyhome-garage-bootstrap.service")
        assert "cloudyhome-garage.service" in u["Unit"]["After"]

    def test_scrub_after_zfs_import(self):
        u = load_unit("cloudyhome-zfs-scrub.service")
        assert "cloudyhome-zfs-import.service" in u["Unit"]["After"]


class TestServiceProperties:
    def test_all_oneshot(self):
        """Boot-chain services are oneshot."""
        for name in (
            "cloudyhome-nas-validate.service",
            "cloudyhome-zfs-import.service",
            "cloudyhome-nas-render.service",
            "cloudyhome-nas-firewall.service",
            "cloudyhome-nas-apply.service",
            "cloudyhome-zfs-scrub.service",
            "cloudyhome-garage-bootstrap.service",
        ):
            u = load_unit(name)
            assert u["Service"]["Type"] == "oneshot", f"{name} should be oneshot"

    def test_remain_after_exit(self):
        """Chain services that gate others should RemainAfterExit."""
        for name in (
            "cloudyhome-nas-validate.service",
            "cloudyhome-zfs-import.service",
            "cloudyhome-nas-render.service",
            "cloudyhome-nas-firewall.service",
            "cloudyhome-nas-apply.service",
        ):
            u = load_unit(name)
            assert u["Service"].get("RemainAfterExit") == "yes", f"{name} should RemainAfterExit"

    def test_sops_env_on_secret_services(self):
        """Services that decrypt secrets need SOPS_AGE_KEY_FILE."""
        for name in (
            "cloudyhome-nas-validate.service",
            "cloudyhome-zfs-import.service",
            "cloudyhome-nas-render.service",
            "cloudyhome-nas-apply.service",
            "cloudyhome-garage-bootstrap.service",
        ):
            u = load_unit(name)
            env = u["Service"].get("Environment", "")
            assert "SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt" in env, f"{name} missing SOPS_AGE_KEY_FILE"

    def test_wanted_by_multi_user(self):
        """Boot-chain services should be wanted by multi-user.target."""
        for name in (
            "cloudyhome-nas-validate.service",
            "cloudyhome-zfs-import.service",
            "cloudyhome-nas-render.service",
            "cloudyhome-nas-apply.service",
        ):
            u = load_unit(name)
            wanted = u["Install"]["WantedBy"] if u.has_section("Install") else ""
            assert "multi-user.target" in wanted, f"{name} missing WantedBy"

    def test_firewall_wanted_by_multi_user(self):
        u = load_unit("cloudyhome-nas-firewall.service")
        assert "multi-user.target" in u["Install"]["WantedBy"]


class TestTimerUnit:
    def test_scrub_timer_calendar(self):
        u = load_unit("cloudyhome-zfs-scrub.timer")
        assert "*-*-1,15 02:00:00" in u["Timer"]["OnCalendar"]

    def test_scrub_timer_persistent(self):
        u = load_unit("cloudyhome-zfs-scrub.timer")
        assert u["Timer"]["Persistent"] == "true"

    def test_scrub_timer_wanted_by(self):
        u = load_unit("cloudyhome-zfs-scrub.timer")
        assert "timers.target" in u["Install"]["WantedBy"]


class TestScriptPaths:
    """Verify ExecStart points to the correct scripts."""

    EXPECTED = {
        "cloudyhome-nas-validate.service": "/usr/local/sbin/nas-validate-config",
        "cloudyhome-zfs-import.service": "/usr/local/sbin/nas-zfs-import",
        "cloudyhome-nas-render.service": "/usr/local/sbin/nas-render-config",
        "cloudyhome-nas-firewall.service": "/usr/sbin/nft -f /etc/nftables.conf",
        "cloudyhome-nas-apply.service": "/usr/local/sbin/nas-apply-config",
        "cloudyhome-garage-bootstrap.service": "/usr/local/sbin/nas-garage-bootstrap",
        "cloudyhome-zfs-scrub.service": "/usr/sbin/zpool scrub zpool0",
    }

    @pytest.mark.parametrize("unit,expected_exec", EXPECTED.items())
    def test_exec_start(self, unit, expected_exec):
        u = load_unit(unit)
        assert u["Service"]["ExecStart"] == expected_exec
