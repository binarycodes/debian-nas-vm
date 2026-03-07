"""Tests for secrets resolution (secrets.py)."""
import pytest
from cloudyhome.secrets import resolve_ref


class TestResolveRef:
    def test_simple_key(self):
        assert resolve_ref({"host": {"ip": "10.0.0.1"}}, "host/ip") == "10.0.0.1"

    def test_nested_key(self):
        secrets = {"a": {"b": {"c": "deep"}}}
        assert resolve_ref(secrets, "a/b/c") == "deep"

    def test_top_level_key(self):
        secrets = {"allowed_email_domains": ["example.com"]}
        assert resolve_ref(secrets, "allowed_email_domains") == ["example.com"]

    def test_missing_key_raises(self):
        with pytest.raises(KeyError, match="not found"):
            resolve_ref({"host": {}}, "host/ip")

    def test_missing_intermediate_raises(self):
        with pytest.raises(KeyError, match="not found"):
            resolve_ref({}, "host/ip")

    def test_non_dict_intermediate_raises(self):
        with pytest.raises(KeyError, match="not found"):
            resolve_ref({"host": "string"}, "host/ip")

    def test_all_secrets_example_refs(self, services_raw, secrets_raw):
        """Verify every _ref field in services.yml resolves against the example secrets."""
        # host_ip_ref
        assert resolve_ref(secrets_raw, services_raw["host_ip_ref"])

        # firewall sources
        for rule in services_raw["firewall"]["rules"]:
            result = resolve_ref(secrets_raw, rule["sources_ref"])
            assert isinstance(result, list)

        # NFS CIDRs
        for export in services_raw["nfs"]["exports"]:
            for client in export["clients"]:
                result = resolve_ref(secrets_raw, client["cidr_ref"])
                assert isinstance(result, list)

        # Samba users
        for share in services_raw["samba"]["shares"]:
            for ref in share["users_ref"]:
                resolve_ref(secrets_raw, ref)

        # iSCSI CHAP
        for target in services_raw["iscsi"]["targets"]:
            if target["auth"]["session_auth"] == "chap":
                result = resolve_ref(secrets_raw, target["auth"]["chap_secret_ref"])
                assert "chap_user" in result
                assert "chap_password" in result

        # Garage
        resolve_ref(secrets_raw, services_raw["garage"]["admin_token_ref"])
        resolve_ref(secrets_raw, services_raw["garage"]["rpc_secret_ref"])

        # FTP
        for ref in services_raw["ftp"]["users_ref"]:
            resolve_ref(secrets_raw, ref)

        # Health
        resolve_ref(secrets_raw, services_raw["health"]["alert"]["smtp_auth_ref"])
        resolve_ref(secrets_raw, services_raw["health"]["alert"]["addresses_ref"])
