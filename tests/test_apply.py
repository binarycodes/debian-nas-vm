"""Tests for nas-apply-config logic (parse_size_bytes and structural tests)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "lib", "cloudyhome"))

# Import from the apply script (no .py extension, so we use SourceFileLoader directly)
APPLY_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "sbin", "nas-apply-config")

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

    def test_daemon_reload_first(self):
        content = open(APPLY_SCRIPT).read()
        # Within main(), daemon-reload should appear before create_datasets call
        main_body = content[content.index("def main()"):]
        reload_pos = main_body.index("daemon-reload")
        dataset_pos = main_body.index("create_datasets")
        assert reload_pos < dataset_pos

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
        assert "/etc/cloudyhome/nas-apply-services.sh" in content

    def test_enables_stock_services(self):
        content = open(APPLY_SCRIPT).read()
        main_body = content[content.index("def main()"):]
        assert '"enable", "smartd.service"' in main_body
        assert '"enable", "zfs-zed.service"' in main_body

    def test_stock_enable_before_datasets(self):
        content = open(APPLY_SCRIPT).read()
        main_body = content[content.index("def main()"):]
        enable_pos = main_body.index("smartd.service")
        dataset_pos = main_body.index("create_datasets")
        assert enable_pos < dataset_pos
