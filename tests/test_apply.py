"""Tests for nas-apply-config logic (parse_size_bytes and structural tests)."""
import os
from unittest.mock import MagicMock

import pytest

# Import from the apply script (no .py extension, so we use SourceFileLoader directly)
APPLY_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "sbin", "nas-apply-config")
APPLY_SERVICES_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "nas_root", "etc", "cloudyhome", "templates", "nas-apply-services.sh.j2")

import importlib.util
from importlib.machinery import SourceFileLoader
_loader = SourceFileLoader("nas_apply_config", APPLY_SCRIPT)
_spec = importlib.util.spec_from_file_location("nas_apply_config", APPLY_SCRIPT, loader=_loader)
apply_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_mod)


class TestParseSizeBytes:
    def test_gigabytes(self):
        assert apply_mod.parse_size_bytes("500G") == 500 * 1024**3

    def test_terabytes(self):
        assert apply_mod.parse_size_bytes("2T") == 2 * 1024**4

    def test_megabytes(self):
        assert apply_mod.parse_size_bytes("100M") == 100 * 1024**2

    def test_kilobytes(self):
        assert apply_mod.parse_size_bytes("1024K") == 1024 * 1024


class TestApplyStructure:
    """Verify apply script has correct lock, ordering, and idempotent patterns."""

    def test_uses_flock(self):
        content = open(APPLY_SCRIPT).read()
        assert "fcntl.flock" in content
        assert "LOCK_EX" in content

    def test_daemon_reload_in_shell_script(self):
        content = open(APPLY_SERVICES_TEMPLATE).read()
        assert "daemon-reload" in content

    def test_daemon_reload_before_enable_in_shell_script(self):
        content = open(APPLY_SERVICES_TEMPLATE).read()
        reload_pos = content.index("daemon-reload")
        enable_pos = content.index("systemctl enable")
        assert reload_pos < enable_pos

    def test_datasets_before_zvols(self):
        content = open(APPLY_SCRIPT).read()
        datasets_pos = content.index("create_datasets(config)")
        zvols_pos = content.index("create_zvols(config)")
        assert datasets_pos < zvols_pos

    def test_samba_users_provisioned(self):
        content = open(APPLY_SCRIPT).read()
        assert "provision_samba_users" in content
        assert "useradd" in content
        assert "smbpasswd" in content
        assert "pdbedit" in content

    def test_apply_services_script_called(self):
        content = open(APPLY_SCRIPT).read()
        assert "APPLY_SERVICES_SCRIPT" in content

    def test_enables_stock_services(self):
        content = open(APPLY_SERVICES_TEMPLATE).read()
        assert "enable smartd.service" in content
        assert "enable zfs-zed.service" in content

    def test_stock_enable_before_reload_or_restart(self):
        content = open(APPLY_SERVICES_TEMPLATE).read()
        enable_pos = content.index("enable smartd.service")
        restart_pos = content.index("reload-or-restart smartd.service")
        assert enable_pos < restart_pos


# ---------------------------------------------------------------------------
# Functional logic tests (with mocked subprocess)
# ---------------------------------------------------------------------------

_MINIMAL_RAW = {
    "version": 2,
    "host_ip_ref": "host/ip",
    "storage": [
        {"pool": "zpool0", "datasets": {"data": {"path": "/zpool0/data", "quota": "10G"}}},
    ],
    "firewall": {
        "default_input": "drop",
        "rules": [{"service": "ssh", "ports": [22], "proto": ["tcp"], "sources_ref": "fw/ssh"}],
    },
}


class TestParseSizeBytesExtra:
    def test_bare_integer(self):
        assert apply_mod.parse_size_bytes("1048576") == 1048576


class TestCreateDatasets:
    @pytest.fixture
    def config(self):
        from cloudyhome.models import NasConfig
        return NasConfig(**_MINIMAL_RAW)

    def test_creates_missing_dataset(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        assert any(c[:2] == ["zfs", "create"] for c in calls)

    def test_creates_with_compression_lz4(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        create_cmd = next(c for c in calls if c[:2] == ["zfs", "create"])
        assert "compression=lz4" in create_cmd

    def test_creates_with_atime_off(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        create_cmd = next(c for c in calls if c[:2] == ["zfs", "create"])
        assert "atime=off" in create_cmd

    def test_creates_with_dedup_off(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        create_cmd = next(c for c in calls if c[:2] == ["zfs", "create"])
        assert "dedup=off" in create_cmd

    def test_creates_with_sync_standard(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        create_cmd = next(c for c in calls if c[:2] == ["zfs", "create"])
        assert "sync=standard" in create_cmd

    def test_creates_with_casesensitivity_sensitive(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        create_cmd = next(c for c in calls if c[:2] == ["zfs", "create"])
        assert "casesensitivity=sensitive" in create_cmd

    def test_skips_create_when_dataset_exists(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        assert not any(c[:2] == ["zfs", "create"] for c in calls)

    def test_sets_quota_when_unset(self, config, monkeypatch):
        calls = []
        target_bytes = str(10 * 1024**3)
        quota_reads = []

        def mock_zfs_get(prop, name):
            if prop == "quota":
                quota_reads.append(1)
                # First read: unset; subsequent reads (verification): target applied
                return "0" if len(quota_reads) == 1 else target_bytes
            return "0"  # used

        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", mock_zfs_get)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        assert any("quota=10G" in arg for cmd in calls for arg in cmd)

    def test_raises_when_quota_verification_fails(self, config, monkeypatch):
        # Simulate zfs set succeeding but read-back returning wrong value
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: "0")
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: None)

        with pytest.raises(RuntimeError, match="Quota verification failed"):
            apply_mod.create_datasets(config)

    def test_skips_quota_when_already_correct(self, config, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        assert not any("quota" in arg for cmd in calls for arg in cmd)

    def test_raises_when_lowering_quota_below_usage(self, config, monkeypatch):
        current_quota = 20 * 1024**3
        used = 15 * 1024**3

        def mock_zfs_get(prop, name):
            return str(current_quota) if prop == "quota" else str(used)

        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", mock_zfs_get)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: None)

        with pytest.raises(RuntimeError, match="Cannot set quota"):
            apply_mod.create_datasets(config)

    def test_raises_when_setting_quota_from_none_and_usage_exceeds_target(self, config, monkeypatch):
        # current quota is none/0, but dataset already uses more than the target
        used = 15 * 1024**3

        def mock_zfs_get(prop, name):
            return "0" if prop == "quota" else str(used)

        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "zfs_get", mock_zfs_get)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: None)

        with pytest.raises(RuntimeError, match="Cannot set quota"):
            apply_mod.create_datasets(config)

    def test_creates_datasets_across_two_pools(self, monkeypatch):
        from cloudyhome.models import NasConfig
        two_pool_raw = {
            "version": 2,
            "host_ip_ref": "host/ip",
            "storage": [
                {"pool": "zpool0", "datasets": {"data": {"path": "/zpool0/data", "quota": "10G"}}},
                {"pool": "tank", "datasets": {"media": {"path": "/tank/media", "quota": "10G"}}},
            ],
            "firewall": {
                "default_input": "drop",
                "rules": [{"service": "ssh", "ports": [22], "proto": ["tcp"], "sources_ref": "fw/ssh"}],
            },
        }
        config = NasConfig(**two_pool_raw)
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "zfs_get", lambda prop, name: str(10 * 1024**3))
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_datasets(config)

        created = [c for c in calls if c[:2] == ["zfs", "create"]]
        assert any("zpool0/data" in arg for cmd in created for arg in cmd)
        assert any("tank/media" in arg for cmd in created for arg in cmd)


class TestCreateZvols:
    def test_creates_missing_zvol(self, services_raw, monkeypatch):
        from cloudyhome.models import NasConfig
        config = NasConfig(**services_raw)
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_zvols(config)

        assert any(c[:3] == ["zfs", "create", "-V"] for c in calls)

    def test_zvol_path_uses_iscsi_dataset(self, services_raw, monkeypatch):
        from cloudyhome.models import NasConfig
        config = NasConfig(**services_raw)
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: False)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_zvols(config)

        # Full ZFS path must be derived from iscsi.dataset + lun.path, not storage.pool
        assert any("zpool0/iscsi/vmstore" in arg for cmd in calls for arg in cmd)

    def test_skips_existing_zvol(self, services_raw, monkeypatch):
        from cloudyhome.models import NasConfig
        config = NasConfig(**services_raw)
        calls = []
        monkeypatch.setattr(apply_mod, "dataset_exists", lambda _: True)
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_zvols(config)

        assert not any(c[:2] == ["zfs", "create"] for c in calls)

    def test_noop_when_no_iscsi(self, monkeypatch):
        from cloudyhome.models import NasConfig
        config = NasConfig(**_MINIMAL_RAW)
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.create_zvols(config)

        assert calls == []


class TestProvisionSambaUsers:
    @pytest.fixture
    def samba_config(self, services_raw):
        from cloudyhome.models import NasConfig
        return NasConfig(**services_raw)

    @pytest.fixture
    def samba_secrets(self):
        return {"samba": {"users": {"smb_alice": {"password": "secret"}}}}

    def _make_run_cmd(self, calls, user_exists, samba_user_exists):
        def run_cmd(cmd, input_data=None, check=True):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            if cmd[0] == "id":
                result.returncode = 0 if user_exists else 1
            elif cmd[0] == "pdbedit":
                result.returncode = 0 if samba_user_exists else 1
            return result
        return run_cmd

    def test_creates_new_os_user_when_absent(self, samba_config, samba_secrets, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", self._make_run_cmd(calls, user_exists=False, samba_user_exists=False))

        apply_mod.provision_samba_users(samba_config, samba_secrets)

        assert any(c[0] == "useradd" for c in calls)

    def test_skips_useradd_when_user_exists(self, samba_config, samba_secrets, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", self._make_run_cmd(calls, user_exists=True, samba_user_exists=True))

        apply_mod.provision_samba_users(samba_config, samba_secrets)

        assert not any(c[0] == "useradd" for c in calls)

    def test_adds_new_samba_user_with_smbpasswd_a(self, samba_config, samba_secrets, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", self._make_run_cmd(calls, user_exists=True, samba_user_exists=False))

        apply_mod.provision_samba_users(samba_config, samba_secrets)

        assert any(c == ["smbpasswd", "-a", "-s", "smb_alice"] for c in calls)

    def test_updates_existing_samba_user(self, samba_config, samba_secrets, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", self._make_run_cmd(calls, user_exists=True, samba_user_exists=True))

        apply_mod.provision_samba_users(samba_config, samba_secrets)

        assert any(c == ["smbpasswd", "-s", "smb_alice"] for c in calls)

    def test_noop_when_no_samba(self, monkeypatch):
        from cloudyhome.models import NasConfig
        config = NasConfig(**_MINIMAL_RAW)
        calls = []
        monkeypatch.setattr(apply_mod, "run_cmd", lambda cmd, **kw: calls.append(cmd))

        apply_mod.provision_samba_users(config, {})

        assert calls == []
