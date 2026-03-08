"""Tests for template rendering (render.py + nas-render-config build_context/build_saveconfig)."""
import json
import os
import sys
import tempfile

import pytest
import yaml

from cloudyhome.models import NasConfig
from cloudyhome.secrets import resolve_ref
from cloudyhome.render import render_template, atomic_write, get_jinja_env

# Import build functions from the render script (no .py extension)
RENDER_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "sbin", "nas-render-config")

import importlib.util
from importlib.machinery import SourceFileLoader
_loader = SourceFileLoader("nas_render_config", RENDER_SCRIPT)
_spec = importlib.util.spec_from_file_location("nas_render_config", RENDER_SCRIPT, loader=_loader)
render_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_mod)
build_context = render_mod.build_context
build_saveconfig = render_mod.build_saveconfig


@pytest.fixture
def full_context(services_raw, secrets_raw):
    config = NasConfig(**services_raw)
    return build_context(config, secrets_raw), config


class TestBuildContext:
    def test_host_ip_resolved(self, full_context):
        ctx, config = full_context
        assert ctx["host_ip"] == "10.0.0.10"

    def test_firewall_sources_resolved(self, full_context):
        ctx, config = full_context
        assert "ssh" in ctx["firewall_sources"]
        assert isinstance(ctx["firewall_sources"]["ssh"], list)

    def test_nfs_cidrs_resolved(self, full_context):
        ctx, config = full_context
        assert "media" in ctx["nfs_cidrs"]
        assert 0 in ctx["nfs_cidrs"]["media"]

    def test_samba_usernames(self, full_context):
        ctx, config = full_context
        assert "media" in ctx["samba_usernames"]
        assert "smb_alice" in ctx["samba_usernames"]["media"]

    def test_iscsi_chap(self, full_context):
        ctx, config = full_context
        assert "vmstore" in ctx["iscsi_chap"]
        assert "chap_user" in ctx["iscsi_chap"]["vmstore"]

    def test_garage_secrets(self, full_context):
        ctx, config = full_context
        assert ctx["garage_rpc_secret"] == "REDACTED"
        assert ctx["garage_admin_token"] == "REDACTED"

    def test_ftp_users_env(self, full_context):
        ctx, config = full_context
        assert "scanner1|" in ctx["ftp_users_env"]

    def test_health_addresses(self, full_context):
        ctx, config = full_context
        assert "@" in ctx["alert_to"]
        assert "@" in ctx["alert_from"]


class TestBuildSaveconfig:
    def test_structure(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        assert "fabric_modules" in result
        assert "storage_objects" in result
        assert "targets" in result

    def test_target_iqn(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        assert result["targets"][0]["wwn"] == "iqn.2026-03.home.arpa:nas01:vmstore"

    def test_storage_object_dev_path(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        so = result["storage_objects"][0]
        assert so["dev"] == "/dev/zvol/zpool0/iscsi/vmstore"
        assert so["plugin"] == "block"

    def test_chap_auth_on_acl(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        acl = result["targets"][0]["tpgs"][0]["node_acls"][0]
        assert acl["chap_userid"] == "vmstore-user"
        assert "chap_password" in acl

    def test_portal(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        portal = result["targets"][0]["tpgs"][0]["portals"][0]
        assert portal["ip_address"] == "10.0.0.10"
        assert portal["port"] == 3260

    def test_authentication_attribute(self, full_context):
        ctx, config = full_context
        result = build_saveconfig(config, ctx)
        attrs = result["targets"][0]["tpgs"][0]["attributes"]
        assert attrs["authentication"] == 1  # CHAP enabled

    def test_disabled_target_excluded(self, services_raw, secrets_raw):
        services_raw["iscsi"]["targets"][0]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        result = build_saveconfig(config, ctx)
        assert result["targets"] == []
        assert result["storage_objects"] == []


class TestTemplateRendering:
    def test_nftables_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("nftables.conf.j2", ctx, template_dir)
        assert "flush ruleset" in content
        assert "table inet filter" in content
        assert "policy drop" in content
        assert "tcp dport 22" in content
        assert "10.0.0.0/24" in content
        assert 'comment "ssh"' in content
        # Port range for ftp-passive
        assert "21000-21010" in content

    def test_exports_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("exports.j2", ctx, template_dir)
        assert "/zpool0/shares/media" in content
        assert "10.0.0.0/24" in content
        assert "rw" in content
        assert "root_squash" in content

    def test_smb_conf_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "workgroup = WORKGROUP" in content
        assert "server min protocol = SMB3_11" in content
        assert "[media]" in content
        assert "valid users = smb_alice" in content
        assert "interfaces = 10.0.0.1" in content

    def test_garage_toml_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("garage.toml.j2", ctx, template_dir)
        assert 'metadata_dir = "/zpool0/system/garage/meta"' in content
        assert 'data_dir = "/zpool0/system/garage/data"' in content
        assert 'replication_mode = "none"' in content
        assert 'rpc_secret = "REDACTED"' in content
        assert 'admin_token = "REDACTED"' in content

    def test_ftp_env_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("ftp.env.j2", ctx, template_dir)
        assert "USERS=scanner1|REDACTED" in content
        assert "ADDRESS=10.0.0.1" in content
        assert "MIN_PORT=21000" in content
        assert "MAX_PORT=21010" in content

    def test_garage_container_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("cloudyhome-garage.container.j2", ctx, template_dir)
        assert "ContainerName=cloudyhome-garage" in content
        assert "Image=dxflrs/garage:latest" in content
        assert "Network=host" in content

    def test_ftp_container_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("cloudyhome-ftp.container.j2", ctx, template_dir)
        assert "ContainerName=cloudyhome-ftp" in content
        assert "PublishPort=10.0.0.10:21:21" in content

    def test_alert_conf_enabled(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("alert.conf.j2", ctx, template_dir)
        assert "ALERT_ENABLED=true" in content
        assert "ALERT_TO=admin@example.com" in content
        assert "ALERT_FROM=nas@example.com" in content

    def test_alert_conf_disabled(self, services_raw, secrets_raw, template_dir):
        services_raw["health"]["alert"]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("alert.conf.j2", ctx, template_dir)
        assert "ALERT_ENABLED=false" in content

    def test_msmtprc_renders(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("msmtprc.j2", ctx, template_dir)
        assert "host           smtp.example.com" in content
        assert "port           587" in content
        assert "tls            on" in content
        assert "tls_starttls   on" in content

    def test_apply_services_script(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("nas-apply-services.sh.j2", ctx, template_dir)
        assert "nfs-server.service" in content
        assert "smbd.service" in content
        assert "target.service" in content
        assert "cloudyhome-garage.service" in content
        assert "cloudyhome-ftp.service" in content
        assert "smartd.service" in content
        assert "zfs-zed.service" in content
        assert "cloudyhome-zfs-scrub.timer" in content

    def test_apply_services_without_optional(self, services_raw, secrets_raw, template_dir):
        """When optional services are absent, their systemctl lines are omitted."""
        for key in ("nfs", "samba", "iscsi", "garage", "ftp"):
            del services_raw[key]
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("nas-apply-services.sh.j2", ctx, template_dir)
        assert "nfs-server" not in content
        assert "smbd" not in content
        assert "target.service" not in content
        assert "cloudyhome-garage" not in content
        assert "cloudyhome-ftp" not in content
        # Health monitoring always present
        assert "smartd.service" in content
        assert "zfs-zed.service" in content


class TestAtomicWrite:
    """Test atomic_write with a patched version that skips chown (not root in test env)."""

    @pytest.fixture(autouse=True)
    def _patch_chown(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "chown", lambda *a, **kw: None)

    def test_creates_file(self, tmp_path):
        dest = str(tmp_path / "test.conf")
        run_dir = str(tmp_path / "run")
        result = atomic_write("hello\n", dest, run_dir=run_dir)
        assert result is True
        assert open(dest).read() == "hello\n"

    def test_unchanged_returns_false(self, tmp_path):
        dest = str(tmp_path / "test.conf")
        run_dir = str(tmp_path / "run")
        atomic_write("hello\n", dest, run_dir=run_dir)
        result = atomic_write("hello\n", dest, run_dir=run_dir)
        assert result is False

    def test_changed_returns_true(self, tmp_path):
        dest = str(tmp_path / "test.conf")
        run_dir = str(tmp_path / "run")
        atomic_write("hello\n", dest, run_dir=run_dir)
        result = atomic_write("world\n", dest, run_dir=run_dir)
        assert result is True
        assert open(dest).read() == "world\n"

    def test_validator_called(self, tmp_path):
        dest = str(tmp_path / "test.conf")
        run_dir = str(tmp_path / "run")

        def bad_validator(path):
            raise RuntimeError("validation failed")

        with pytest.raises(RuntimeError, match="validation failed"):
            atomic_write("hello\n", dest, run_dir=run_dir, validator=bad_validator)
        # File should not exist after failed validation
        assert not os.path.exists(dest)

    def test_creates_parent_dirs(self, tmp_path):
        dest = str(tmp_path / "sub" / "dir" / "test.conf")
        run_dir = str(tmp_path / "run")
        atomic_write("hello\n", dest, run_dir=run_dir)
        assert open(dest).read() == "hello\n"


class TestBuildContextOptional:
    def test_absent_sections_produce_no_context_keys(self, services_raw, secrets_raw):
        for key in ("nfs", "samba", "iscsi", "garage", "ftp"):
            del services_raw[key]
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        assert "nfs_cidrs" not in ctx
        assert "samba_usernames" not in ctx
        assert "iscsi_chap" not in ctx
        assert "garage_rpc_secret" not in ctx
        assert "ftp_users_env" not in ctx


class TestBuildSaveconfigEdgeCases:
    def test_no_chap_sets_authentication_zero(self, services_raw, secrets_raw):
        services_raw["iscsi"]["targets"][0]["auth"]["session_auth"] = "none"
        services_raw["iscsi"]["targets"][0]["auth"]["chap_secret_ref"] = ""
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        result = build_saveconfig(config, ctx)
        attrs = result["targets"][0]["tpgs"][0]["attributes"]
        assert attrs["authentication"] == 0

    def test_no_initiators_denies_all(self, services_raw, secrets_raw):
        services_raw["iscsi"]["targets"][0]["initiators"] = []
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        result = build_saveconfig(config, ctx)
        tpg = result["targets"][0]["tpgs"][0]
        assert tpg["attributes"]["generate_node_acls"] == 0
        assert tpg["node_acls"] == []


class TestExportsTemplateVariants:
    def test_disabled_export_excluded(self, services_raw, secrets_raw, template_dir):
        services_raw["nfs"]["exports"][0]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("exports.j2", ctx, template_dir)
        assert "/zpool0/shares/media" not in content

    def test_no_root_squash_identity_map(self, services_raw, secrets_raw, template_dir):
        services_raw["nfs"]["exports"][0]["clients"][0]["identity_map"] = {"mode": "no_root_squash"}
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("exports.j2", ctx, template_dir)
        assert "no_root_squash" in content

    def test_all_squash_identity_map(self, services_raw, secrets_raw, template_dir):
        services_raw["nfs"]["exports"][0]["clients"][0]["identity_map"] = {
            "mode": "all_squash",
            "anon_uid": 65534,
            "anon_gid": 65534,
        }
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("exports.j2", ctx, template_dir)
        assert "all_squash" in content
        assert "anonuid=65534" in content
        assert "anongid=65534" in content


class TestSmbConfTemplateVariants:
    def test_disabled_share_excluded(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "[media]" not in content

    def test_read_only_share(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["read_only"] = True
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "read only = yes" in content

    def test_guest_ok_share(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["guest_ok"] = True
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "guest ok = yes" in content

    def test_write_list_rendered(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["write_list"] = ["smb_alice"]
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "write list = smb_alice" in content

    def test_force_user_rendered(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["force_user"] = "media"
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "force user = media" in content

    def test_force_group_rendered(self, services_raw, secrets_raw, template_dir):
        services_raw["samba"]["shares"][0]["force_group"] = "media"
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("smb.conf.j2", ctx, template_dir)
        assert "force group = media" in content


class TestMsmtprcTemplateVariants:
    def test_tls_mode(self, services_raw, secrets_raw, template_dir):
        services_raw["health"]["alert"]["smtp_tls"] = "tls"
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("msmtprc.j2", ctx, template_dir)
        assert "tls            on" in content
        assert "tls_starttls   off" in content

    def test_tls_off_mode(self, services_raw, secrets_raw, template_dir):
        services_raw["health"]["alert"]["smtp_tls"] = "off"
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("msmtprc.j2", ctx, template_dir)
        assert "tls            off" in content


class TestNftablesTemplateVariants:
    def test_bare_ip_source_expands_to_cidr(self, full_context, template_dir):
        ctx, config = full_context
        content = render_template("nftables.conf.j2", ctx, template_dir)
        # FTP source is 192.168.1.50 (bare IP), must be expanded to /32
        assert "192.168.1.50/32" in content


class TestApplyServicesDisabledVariants:
    def test_garage_disabled_excluded(self, services_raw, secrets_raw, template_dir):
        services_raw["garage"]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("nas-apply-services.sh.j2", ctx, template_dir)
        assert "cloudyhome-garage" not in content

    def test_ftp_disabled_excluded(self, services_raw, secrets_raw, template_dir):
        services_raw["ftp"]["enabled"] = False
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("nas-apply-services.sh.j2", ctx, template_dir)
        assert "cloudyhome-ftp" not in content


class TestExportsTemplateFallback:
    def test_export_level_options_used_when_client_options_empty(self, services_raw, secrets_raw, template_dir):
        # Set client options to empty so export-level options are used as fallback
        services_raw["nfs"]["exports"][0]["clients"][0]["options"] = []
        services_raw["nfs"]["exports"][0]["options"] = ["ro", "async"]
        config = NasConfig(**services_raw)
        ctx = build_context(config, secrets_raw)
        content = render_template("exports.j2", ctx, template_dir)
        assert "ro" in content
        assert "async" in content


class TestRenderStructure:
    def test_uses_flock(self):
        content = open(RENDER_SCRIPT).read()
        assert "fcntl.flock" in content
        assert "LOCK_EX" in content
