"""Tests for Pydantic schema validation (models.py)."""
import copy
import pytest
from pydantic import ValidationError
from cloudyhome.models import (
    NasConfig, StorageConfig, StorageDataset, FirewallConfig, FirewallRule,
    NfsConfig, NfsExport, NfsClient, IdentityMap, SambaConfig, SambaShare,
    SambaGlobal, IscsiConfig, IscsiTarget, IscsiLun, IscsiAuth,
    GarageConfig, FtpConfig, HealthConfig, HealthAlert,
    SecretsConfig,
)


class TestNasConfigTopLevel:
    def test_valid_full_config(self, services_raw):
        config = NasConfig(**services_raw)
        assert config.version == 1
        assert config.host_ip_ref == "host/ip"

    def test_version_must_be_1(self, services_raw):
        services_raw["version"] = 2
        with pytest.raises(ValidationError, match="version must be 1"):
            NasConfig(**services_raw)

    def test_host_ip_ref_required(self, services_raw):
        services_raw["host_ip_ref"] = ""
        with pytest.raises(ValidationError, match="host_ip_ref must be non-empty"):
            NasConfig(**services_raw)

    def test_extra_fields_forbidden(self, services_raw):
        services_raw["bogus_field"] = "nope"
        with pytest.raises(ValidationError):
            NasConfig(**services_raw)

    def test_optional_sections_can_be_none(self, services_raw):
        for key in ("nfs", "samba", "iscsi", "garage", "ftp", "health"):
            services_raw.pop(key, None)
        config = NasConfig(**services_raw)
        assert config.nfs is None
        assert config.samba is None


class TestStorageConfig:
    def test_pool_can_be_any_name(self):
        config = StorageConfig(pool="tank", datasets={"x": {"path": "/tank/x", "quota": "1G"}})
        assert config.pool == "tank"

    def test_datasets_non_empty(self):
        with pytest.raises(ValidationError, match="datasets must be non-empty"):
            StorageConfig(pool="zpool0", datasets={})

    def test_path_must_match_pool(self):
        with pytest.raises(ValidationError, match="path must start with /zpool0/"):
            StorageConfig(pool="zpool0", datasets={"x": {"path": "/tank/data", "quota": "1G"}})

    def test_quota_format(self):
        for valid in ("10G", "500M", "2T", "1024K"):
            d = StorageDataset(path="/zpool0/x", quota=valid)
            assert d.quota == valid
        for invalid in ("10g", "10GB", "10", "abc"):
            with pytest.raises(ValidationError):
                StorageDataset(path="/zpool0/x", quota=invalid)

    def test_unique_dataset_paths(self):
        with pytest.raises(ValidationError, match="dataset paths must be unique"):
            StorageConfig(pool="zpool0", datasets={
                "a": {"path": "/zpool0/same", "quota": "1G"},
                "b": {"path": "/zpool0/same", "quota": "2G"},
            })

    def test_dataset_key_format(self):
        with pytest.raises(ValidationError, match="must be underscore-separated lowercase"):
            StorageConfig(pool="zpool0", datasets={
                "Invalid-Key": {"path": "/zpool0/x", "quota": "1G"},
            })


class TestFirewallConfig:
    def test_default_input_valid(self):
        with pytest.raises(ValidationError, match="default_input must be drop or accept"):
            FirewallConfig(default_input="reject", rules=[
                {"service": "ssh", "ports": [22], "proto": ["tcp"], "sources_ref": "x"}
            ])

    def test_rules_non_empty(self):
        with pytest.raises(ValidationError, match="rules must be non-empty"):
            FirewallConfig(default_input="drop", rules=[])

    def test_unique_service_names(self):
        rule = {"service": "ssh", "ports": [22], "proto": ["tcp"], "sources_ref": "x"}
        with pytest.raises(ValidationError, match="firewall rule service names must be unique"):
            FirewallConfig(default_input="drop", rules=[rule, rule])

    def test_ports_or_port_range_required(self):
        with pytest.raises(ValidationError, match="must define ports or port_range"):
            FirewallRule(service="x", proto=["tcp"], sources_ref="y")

    def test_cannot_have_both_ports_and_port_range(self):
        with pytest.raises(ValidationError, match="cannot define both ports and port_range"):
            FirewallRule(service="x", ports=[22], port_range=[1001, 2000], proto=["tcp"], sources_ref="y")

    def test_port_range_format(self):
        with pytest.raises(ValidationError, match="port_range must be"):
            FirewallRule(service="x", port_range=[1001], proto=["tcp"], sources_ref="y")
        with pytest.raises(ValidationError, match="min must be <= max"):
            FirewallRule(service="x", port_range=[2000, 1001], proto=["tcp"], sources_ref="y")

    def test_proto_validation(self):
        with pytest.raises(ValidationError, match="proto must be tcp or udp"):
            FirewallRule(service="x", ports=[2049], proto=["sctp"], sources_ref="y")

    def test_proto_non_empty(self):
        with pytest.raises(ValidationError, match="proto must be non-empty"):
            FirewallRule(service="x", ports=[22], proto=[], sources_ref="y")

    def test_well_known_ports_allowed(self):
        for port in (21, 22, 80, 443, 445):
            r = FirewallRule(service="x", ports=[port], proto=["tcp"], sources_ref="y")
            assert port in r.ports

    def test_non_well_known_port_below_1001(self):
        with pytest.raises(ValidationError, match="port 810 must be 1001-65535"):
            FirewallRule(service="x", ports=[810], proto=["tcp"], sources_ref="y")


class TestNfsConfig:
    def test_version_must_be_4(self):
        with pytest.raises(ValidationError, match="NFS version must be 4"):
            NfsConfig(version=3, exports=[{
                "name": "x", "path": "/x",
                "clients": [{"cidr_ref": "y"}],
            }])

    def test_exports_non_empty(self):
        with pytest.raises(ValidationError, match="exports must be non-empty"):
            NfsConfig(version=4, exports=[])

    def test_unique_export_names(self):
        export = {"name": "dup", "path": "/a", "clients": [{"cidr_ref": "y"}]}
        export2 = {"name": "dup", "path": "/b", "clients": [{"cidr_ref": "y"}]}
        with pytest.raises(ValidationError, match="export names must be unique"):
            NfsConfig(version=4, exports=[export, export2])

    def test_unique_export_paths(self):
        with pytest.raises(ValidationError, match="export paths must be unique"):
            NfsConfig(version=4, exports=[
                {"name": "a", "path": "/same", "clients": [{"cidr_ref": "y"}]},
                {"name": "b", "path": "/same", "clients": [{"cidr_ref": "y"}]},
            ])

    def test_clients_required_when_enabled(self):
        with pytest.raises(ValidationError, match="clients must be non-empty when export is enabled"):
            NfsExport(name="x", path="/x", enabled=True, clients=[])

    def test_disabled_export_no_clients_ok(self):
        e = NfsExport(name="x", path="/x", enabled=False, clients=[])
        assert not e.enabled

    def test_identity_map_all_squash_requires_anon(self):
        with pytest.raises(ValidationError, match="all_squash requires anon_uid and anon_gid"):
            IdentityMap(mode="all_squash")

    def test_identity_map_all_squash_with_anon(self):
        im = IdentityMap(mode="all_squash", anon_uid=65534, anon_gid=65534)
        assert im.mode == "all_squash"

    def test_identity_in_options_forbidden(self):
        with pytest.raises(ValidationError, match="identity mapping directive"):
            NfsClient(cidr_ref="x", options=["root_squash"])
        with pytest.raises(ValidationError, match="identity mapping directive"):
            NfsClient(cidr_ref="x", options=["anonuid=1000"])


class TestSambaConfig:
    def test_global_keyword_aliasing(self):
        cfg = SambaConfig(**{
            "global": {"workgroup": "WG", "min_protocol": "SMB3_11"},
            "shares": [{"name": "s", "path": "/p", "users_ref": ["samba/users/smb_x"]}],
        })
        assert cfg.global_.workgroup == "WG"

    def test_min_protocol_must_be_smb3_11(self):
        with pytest.raises(ValidationError, match="min_protocol must be SMB3_11"):
            SambaGlobal(workgroup="WG", min_protocol="SMB2")

    def test_shares_non_empty(self):
        with pytest.raises(ValidationError, match="shares must be non-empty"):
            SambaConfig(**{
                "global": {"workgroup": "WG", "min_protocol": "SMB3_11"},
                "shares": [],
            })

    def test_unique_share_names(self):
        share = {"name": "dup", "path": "/p", "users_ref": ["samba/users/smb_x"]}
        with pytest.raises(ValidationError, match="share names must be unique"):
            SambaConfig(**{
                "global": {"workgroup": "WG", "min_protocol": "SMB3_11"},
                "shares": [share, share],
            })

    def test_users_ref_required_when_enabled(self):
        with pytest.raises(ValidationError, match="users_ref must be non-empty when share is enabled"):
            SambaShare(name="s", path="/p", enabled=True, users_ref=[])


class TestIscsiConfig:
    def test_base_iqn_format(self):
        with pytest.raises(ValidationError, match="base_iqn must be in IQN format"):
            IscsiConfig(base_iqn="bad", portal_port=3260, dataset="/zpool0/iscsi", targets=[{
                "name": "t", "iqn_suffix": "s",
                "luns": [{"lun": 0, "type": "zvol", "path": "x", "size": "1G"}],
                "auth": {"discovery_auth": "none", "session_auth": "none"},
            }])

    def test_portal_port_range(self):
        with pytest.raises(ValidationError, match="portal_port must be 1001-65535"):
            IscsiConfig(base_iqn="iqn.2026-03.home.arpa:nas", portal_port=80, dataset="/zpool0/iscsi", targets=[{
                "name": "t", "iqn_suffix": "s",
                "luns": [{"lun": 0, "type": "zvol", "path": "x", "size": "1G"}],
                "auth": {"discovery_auth": "none", "session_auth": "none"},
            }])

    def test_lun_type_must_be_zvol(self):
        with pytest.raises(ValidationError, match="LUN type must be zvol"):
            IscsiLun(lun=0, type="file", path="x", size="1G")

    def test_chap_ref_required_when_chap(self):
        with pytest.raises(ValidationError, match="chap_secret_ref required"):
            IscsiAuth(discovery_auth="none", session_auth="chap", chap_secret_ref="")

    def test_enabled_target_requires_luns(self):
        with pytest.raises(ValidationError, match="luns must be non-empty when target is enabled"):
            IscsiTarget(name="t", iqn_suffix="s", enabled=True, luns=[],
                        auth={"discovery_auth": "none", "session_auth": "none"})

    def test_unique_lun_ids(self):
        with pytest.raises(ValidationError, match="LUN IDs must be unique"):
            IscsiTarget(name="t", iqn_suffix="s", auth={"discovery_auth": "none", "session_auth": "none"},
                        luns=[
                            {"lun": 0, "type": "zvol", "path": "a", "size": "1G"},
                            {"lun": 0, "type": "zvol", "path": "b", "size": "1G"},
                        ])

    def test_unique_lun_paths_across_targets(self):
        target_base = {
            "iqn_suffix": "s1",
            "luns": [{"lun": 0, "type": "zvol", "path": "same", "size": "1G"}],
            "auth": {"discovery_auth": "none", "session_auth": "none"},
        }
        t1 = {**target_base, "name": "t1", "iqn_suffix": "s1"}
        t2 = {**target_base, "name": "t2", "iqn_suffix": "s2"}
        with pytest.raises(ValidationError, match="LUN paths must be unique across all targets"):
            IscsiConfig(base_iqn="iqn.2026-03.home.arpa:nas", portal_port=3260, dataset="/zpool0/iscsi", targets=[t1, t2])


class TestGarageConfig:
    def test_runtime_must_be_podman(self):
        with pytest.raises(ValidationError, match="runtime must be podman-quadlet-root"):
            GarageConfig(
                runtime="docker", image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
                s3_region="g", replication_mode="none", config_dir="/etc/g",
                data_dir="/zpool0/d", metadata_dir="/zpool0/m",
                layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            )

    def test_enabled_requires_quadlet_name(self):
        with pytest.raises(ValidationError, match="quadlet_name required"):
            GarageConfig(
                enabled=True, quadlet_name="", image="img", rpc_port=3901, s3_port=3900,
                admin_port=3903, s3_region="g", replication_mode="none", config_dir="/etc/g",
                data_dir="/zpool0/d", metadata_dir="/zpool0/m",
                layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            )

    def test_config_dir_must_be_absolute(self):
        with pytest.raises(ValidationError, match="config_dir must be absolute"):
            GarageConfig(
                image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
                s3_region="g", replication_mode="none", config_dir="relative/path",
                data_dir="/zpool0/d", metadata_dir="/zpool0/m",
                layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            )

    def test_bootstrap_timeout_default(self):
        g = GarageConfig(
            image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
            s3_region="g", replication_mode="none", config_dir="/etc/g",
            data_dir="/zpool0/d", metadata_dir="/zpool0/m",
            layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
        )
        assert g.bootstrap_timeout == 60

    def test_bootstrap_timeout_configurable(self):
        g = GarageConfig(
            image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
            s3_region="g", replication_mode="none", config_dir="/etc/g",
            data_dir="/zpool0/d", metadata_dir="/zpool0/m",
            layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            bootstrap_timeout=120,
        )
        assert g.bootstrap_timeout == 120

    def test_replication_mode_values(self):
        for valid in ("none", "1", "2", "3"):
            # Just test the validator doesn't raise
            GarageConfig(
                image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
                s3_region="g", replication_mode=valid, config_dir="/etc/g",
                data_dir="/zpool0/d", metadata_dir="/zpool0/m",
                layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            )
        with pytest.raises(ValidationError, match="replication_mode"):
            GarageConfig(
                image="img", rpc_port=3901, s3_port=3900, admin_port=3903,
                s3_region="g", replication_mode="4", config_dir="/etc/g",
                data_dir="/zpool0/d", metadata_dir="/zpool0/m",
                layout_capacity="1G", admin_token_ref="x", rpc_secret_ref="y",
            )


class TestFtpConfig:
    def test_control_port_must_be_21(self):
        with pytest.raises(ValidationError, match="control_port must be 21"):
            FtpConfig(
                image="img", config_dir="/etc/f", control_port=2121,
                users_ref=["ftp/users/u"], upload_root="/zpool0/ftp",
            )

    def test_config_dir_must_be_absolute(self):
        with pytest.raises(ValidationError, match="config_dir must be absolute"):
            FtpConfig(
                image="img", config_dir="relative/path", control_port=21,
                users_ref=["ftp/users/u"], upload_root="/zpool0/ftp",
            )


class TestHealthConfig:
    def test_enabled_requires_smtp_host(self):
        with pytest.raises(ValidationError, match="smtp_host required"):
            HealthAlert(enabled=True, smtp_auth_ref="x", addresses_ref="y")

    def test_enabled_requires_smtp_auth_ref(self):
        with pytest.raises(ValidationError, match="smtp_auth_ref required"):
            HealthAlert(enabled=True, smtp_host="smtp.example.com", smtp_auth_ref="", addresses_ref="x")

    def test_enabled_requires_addresses_ref(self):
        with pytest.raises(ValidationError, match="addresses_ref required"):
            HealthAlert(enabled=True, smtp_host="smtp.example.com", smtp_auth_ref="x", addresses_ref="")

    def test_smtp_tls_values(self):
        for valid in ("starttls", "tls", "off"):
            h = HealthAlert(smtp_tls=valid)
            assert h.smtp_tls == valid
        with pytest.raises(ValidationError, match="smtp_tls must be"):
            HealthAlert(smtp_tls="ssl")

    def test_disabled_alert_no_requirements(self):
        h = HealthAlert(enabled=False)
        assert not h.enabled


class TestSecretsConfig:
    def test_allows_extra_fields(self):
        s = SecretsConfig(host={"ip": "10.0.0.1"}, custom="stuff")
        assert s.model_extra["custom"] == "stuff"
