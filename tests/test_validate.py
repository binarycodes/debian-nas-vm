"""Tests for cross-field validation (validate.py)."""
import copy
import pytest
from cloudyhome.models import NasConfig
from cloudyhome.validate import validate_all, is_rfc1918, validate_ip_rfc1918, validate_email_domain


class TestRfc1918:
    def test_valid_rfc1918(self):
        assert is_rfc1918("10.0.0.0/24")
        assert is_rfc1918("192.168.1.0/24")
        assert is_rfc1918("172.16.0.0/12")
        assert is_rfc1918("10.0.0.1/32")

    def test_invalid_rfc1918(self):
        assert not is_rfc1918("8.8.8.8/32")
        assert not is_rfc1918("1.1.1.0/24")

    def test_bare_ip(self):
        assert is_rfc1918("10.0.0.1")

    def test_invalid_format(self):
        assert not is_rfc1918("not-an-ip")


class TestEmailDomain:
    def test_valid_domain(self):
        assert validate_email_domain("user@example.com", ["example.com"]) is None

    def test_invalid_domain(self):
        err = validate_email_domain("user@evil.com", ["example.com"])
        assert "not in allowed domains" in err

    def test_no_at_sign(self):
        err = validate_email_domain("nope", ["example.com"])
        assert "no @" in err

    def test_case_insensitive(self):
        assert validate_email_domain("user@EXAMPLE.COM", ["example.com"]) is None


class TestValidateAll:
    def test_valid_config_no_errors(self, services_raw, secrets_raw):
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_non_rfc1918_host_ip(self, services_raw, secrets_raw):
        secrets_raw["host"]["ip"] = "8.8.8.8"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Non-RFC1918" in e and "host_ip_ref" in e for e in errors)

    def test_missing_host_ip_ref(self, services_raw, secrets_raw):
        del secrets_raw["host"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("not found" in e for e in errors)

    def test_missing_disk_ids(self, services_raw, secrets_raw):
        del secrets_raw["disks"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("disks.ids not found" in e for e in errors)

    def test_empty_disk_ids(self, services_raw, secrets_raw):
        secrets_raw["disks"]["ids"] = []
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("disks.ids must be a non-empty list" in e for e in errors)

    def test_disk_ids_with_invalid_entries(self, services_raw, secrets_raw):
        secrets_raw["disks"]["ids"] = ["valid-disk-id", None]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("disks.ids must contain only non-empty strings" in e for e in errors)

    def test_disk_ids_with_empty_string_entry(self, services_raw, secrets_raw):
        secrets_raw["disks"]["ids"] = ["valid-disk-id", ""]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("disks.ids must contain only non-empty strings" in e for e in errors)

    def test_non_rfc1918_firewall_source(self, services_raw, secrets_raw):
        secrets_raw["firewall"]["ssh"] = ["8.8.8.8"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Non-RFC1918" in e and "firewall" in e for e in errors)

    def test_nfs_path_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["nfs"]["exports"][0]["path"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("NFS export path" in e and "not in storage.datasets" in e for e in errors)

    def test_nfs_non_rfc1918_cidr(self, services_raw, secrets_raw):
        secrets_raw["nfs"]["media"] = ["8.8.8.0/24"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Non-RFC1918" in e and "NFS" in e for e in errors)

    def test_samba_path_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["samba"]["shares"][0]["path"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Samba share path" in e and "not in storage.datasets" in e for e in errors)

    def test_samba_user_must_have_smb_prefix(self, services_raw, secrets_raw):
        secrets_raw["samba"]["users"] = {"alice": {"password": "pass"}}
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("smb_" in e for e in errors)

    def test_iscsi_dataset_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["iscsi"]["dataset"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("iSCSI dataset" in e and "not in storage.datasets" in e for e in errors)

    def test_iscsi_chap_missing_fields(self, services_raw, secrets_raw):
        secrets_raw["iscsi"]["vmstore"] = {"chap_user": "", "chap_password": ""}
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("missing chap_user" in e or "missing chap_password" in e for e in errors)

    def test_garage_data_dir_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["garage"]["data_dir"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Garage data_dir" in e for e in errors)

    def test_garage_metadata_dir_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["garage"]["metadata_dir"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("Garage metadata_dir" in e for e in errors)

    def test_samba_users_missing_from_secrets(self, services_raw, secrets_raw):
        del secrets_raw["samba"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("samba.users not found" in e for e in errors)

    def test_garage_missing_secret_ref(self, services_raw, secrets_raw):
        del secrets_raw["garage"]["admin_token"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("admin_token_ref" in e for e in errors)

    def test_ftp_upload_root_not_in_datasets(self, services_raw, secrets_raw):
        services_raw["ftp"]["upload_root"] = "/zpool0/nonexistent"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("FTP upload_root" in e for e in errors)

    def test_ftp_missing_users_in_secrets(self, services_raw, secrets_raw):
        del secrets_raw["ftp"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("ftp" in e.lower() for e in errors)

    def test_health_missing_smtp_auth(self, services_raw, secrets_raw):
        del secrets_raw["health"]["smtp_auth"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("smtp_auth_ref" in e for e in errors)

    def test_health_missing_email_addresses(self, services_raw, secrets_raw):
        del secrets_raw["health"]["addresses"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("addresses_ref" in e for e in errors)

    def test_health_email_domain_policy(self, services_raw, secrets_raw):
        secrets_raw["health"]["addresses"]["from_address"] = "nas@evil.org"
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("not in allowed domains" in e for e in errors)

    def test_health_missing_allowed_domains(self, services_raw, secrets_raw):
        del secrets_raw["allowed_email_domains"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        assert any("allowed_email_domains" in e for e in errors)

    def test_health_disabled_no_errors(self, services_raw, secrets_raw):
        services_raw["health"]["alert"]["enabled"] = False
        # Remove health secrets entirely - should still pass
        del secrets_raw["health"]
        del secrets_raw["allowed_email_domains"]
        config = NasConfig(**services_raw)
        errors = validate_all(config, secrets_raw)
        # Only check no health-related errors
        health_errors = [e for e in errors if "health" in e.lower() or "smtp" in e.lower() or "email" in e.lower()]
        assert health_errors == [], f"Unexpected health errors: {health_errors}"
