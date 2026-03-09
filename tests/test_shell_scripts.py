"""Tests for shell scripts - syntax check and content validation."""
import os
import subprocess

import pytest

NAS_ROOT = os.path.join(os.path.dirname(__file__), "..", "nas_root")
SBIN_DIR = os.path.join(NAS_ROOT, "usr", "local", "sbin")


class TestShellSyntax:
    """Verify all shell scripts pass bash -n syntax check."""

    SHELL_SCRIPTS = [
        "nas-zfs-import",
        "nas-health-alert",
        "nas-zedlet-wrapper",
    ]

    @pytest.mark.parametrize("script", SHELL_SCRIPTS)
    def test_syntax(self, script):
        path = os.path.join(SBIN_DIR, script)
        result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error in {script}: {result.stderr}"


class TestPythonSyntax:
    """Verify all Python scripts pass py_compile."""

    PYTHON_SCRIPTS = [
        "nas-validate-config",
        "nas-render-config",
        "nas-apply-config",
        "nas-garage-bootstrap",
    ]

    @pytest.mark.parametrize("script", PYTHON_SCRIPTS)
    def test_syntax(self, script):
        path = os.path.join(SBIN_DIR, script)
        result = subprocess.run(
            ["python3", "-c", f"import py_compile; py_compile.compile('{path}', doraise=True)"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error in {script}: {result.stderr}"


class TestZfsImportScript:
    def test_has_sops_env(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt" in content

    def test_checks_disk_presence(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "/dev/disk/by-id/" in content
        assert "MISSING" in content

    def test_aborts_on_missing_disks(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert 'exit 1' in content
        assert "One or more disks are missing" in content

    def test_set_euo_pipefail(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "set -euo pipefail" in content

    def test_cleanup_trap(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "trap cleanup EXIT" in content

    def test_zfs_mount_all(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "zfs mount -a" in content

    def test_zpool_list_check_before_import(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "zpool list" in content
        list_pos = content.index("zpool list")
        import_pos = content.index("zpool import")
        assert list_pos < import_pos

    def test_zpool_import_command(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "zpool import" in content

    def test_failed_import_exits_1(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "Failed to import pool" in content
        failed_pos = content.index("Failed to import pool")
        # exit 1 must appear after the failed import message
        exit_pos = content.index("exit 1", failed_pos)
        assert exit_pos > failed_pos

    def test_validates_pool_name_not_null(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert 'No pools found in storage configuration' in content or 'Invalid pool name' in content
        # validation must happen before zpool import
        pool_check_pos = content.index("No pools found in storage configuration")
        import_pos = content.index("zpool import")
        assert pool_check_pos < import_pos

    def test_imports_multiple_pools(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert 'storage[].pool' in content

    def test_validates_disk_ids_not_empty(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "No disk IDs found in secrets" in content

    def test_zfs_mount_tolerates_already_mounted(self):
        path = os.path.join(SBIN_DIR, "nas-zfs-import")
        content = open(path).read()
        assert "already mounted" in content


class TestHealthAlertScript:
    def test_set_euo_pipefail(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert "set -euo pipefail" in content

    def test_sources_alert_conf(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert "/etc/cloudyhome/health/alert.conf" in content

    def test_handles_smartd_source(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert "SMARTD_DEVICE" in content
        assert "SMARTD_FAILTYPE" in content

    def test_handles_zed_source(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert "ZEVENT_CLASS" in content
        assert "ZEVENT_SUBCLASS" in content

    def test_journal_logging(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert "logger -t nas-health-alert" in content

    def test_email_conditional(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        assert 'ALERT_ENABLED' in content
        assert "msmtp" in content

    def test_severity_classification(self):
        path = os.path.join(SBIN_DIR, "nas-health-alert")
        content = open(path).read()
        for severity in ("critical", "warning", "info"):
            assert severity in content


class TestZedletWrapper:
    def test_delegates_to_health_alert(self):
        path = os.path.join(SBIN_DIR, "nas-zedlet-wrapper")
        content = open(path).read()
        assert "/usr/local/sbin/nas-health-alert" in content


class TestSmartdConf:
    def test_devicescan(self):
        path = os.path.join(NAS_ROOT, "etc", "smartd.conf")
        content = open(path).read()
        assert "DEVICESCAN" in content
        assert "/usr/local/sbin/nas-health-alert" in content

    def test_schedule(self):
        path = os.path.join(NAS_ROOT, "etc", "smartd.conf")
        content = open(path).read()
        # Short self-test and long self-test schedules present
        assert "-s" in content


class TestZedRc:
    def test_syslog_subclasses(self):
        path = os.path.join(NAS_ROOT, "etc", "zfs", "zed.d", "zed.rc")
        content = open(path).read()
        for subclass in ("scrub_finish", "statechange", "io_error", "resilver_finish"):
            assert subclass in content

    def test_email_disabled(self):
        path = os.path.join(NAS_ROOT, "etc", "zfs", "zed.d", "zed.rc")
        content = open(path).read()
        assert 'ZED_EMAIL_ADDR=""' in content
        assert 'ZED_EMAIL_PROG=""' in content
