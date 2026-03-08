"""Tests for nas-garage-bootstrap logic."""
import json
import os
import sys
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "lib", "cloudyhome"))

BOOTSTRAP_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nas_root", "usr", "local", "sbin", "nas-garage-bootstrap")

import importlib.util
from importlib.machinery import SourceFileLoader
_loader = SourceFileLoader("nas_garage_bootstrap", BOOTSTRAP_SCRIPT)
_spec = importlib.util.spec_from_file_location("nas_garage_bootstrap", BOOTSTRAP_SCRIPT, loader=_loader)
bootstrap_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap_mod)


def _mock_resp(data):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(data).encode()
    return resp


class TestApiRequest:
    def test_sets_bearer_token(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp({})) as mock_urlopen:
            bootstrap_mod.api_request("http://10.0.0.1:3903/v1/status", "mytoken")
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer mytoken"

    def test_default_get_method(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp({})) as mock_urlopen:
            bootstrap_mod.api_request("http://localhost/v1/status", "token")
        req = mock_urlopen.call_args[0][0]
        assert req.method == "GET"

    def test_parses_json_response(self):
        with patch("urllib.request.urlopen", return_value=_mock_resp({"node": "abc123"})):
            result = bootstrap_mod.api_request("http://localhost/v1/status", "token")
        assert result["node"] == "abc123"


class TestRunPodmanExec:
    def _proc(self, returncode=0, stdout="", stderr=""):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_builds_correct_command(self):
        with patch("subprocess.run", return_value=self._proc()) as mock_run:
            bootstrap_mod.run_podman_exec(["layout", "assign", "-z", "garage"])
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["podman", "exec", "cloudyhome-garage", "garage"]
        assert cmd[4:] == ["layout", "assign", "-z", "garage"]

    def test_raises_on_nonzero_exit(self):
        with patch("subprocess.run", return_value=self._proc(returncode=1, stderr="error")):
            with pytest.raises(RuntimeError, match="podman exec failed"):
                bootstrap_mod.run_podman_exec(["layout", "assign"])

    def test_returns_stdout(self):
        with patch("subprocess.run", return_value=self._proc(stdout="node123\n")):
            result = bootstrap_mod.run_podman_exec(["status"])
        assert result == "node123\n"


class TestBootstrapConstants:
    def test_max_attempts(self):
        assert bootstrap_mod.MAX_ATTEMPTS == 30

    def test_poll_interval(self):
        assert bootstrap_mod.POLL_INTERVAL == 1


class TestBootstrapStructure:
    def test_skips_when_garage_not_enabled(self):
        content = open(BOOTSTRAP_SCRIPT).read()
        assert 'not garage.get("enabled")' in content
        assert "Garage not enabled, skipping bootstrap" in content

    def test_skips_when_layout_has_roles(self):
        content = open(BOOTSTRAP_SCRIPT).read()
        assert "Layout already has roles assigned, skipping" in content

    def test_api_url_uses_host_ip_and_admin_port(self):
        content = open(BOOTSTRAP_SCRIPT).read()
        assert "host_ip" in content
        assert "admin_port" in content
        assert "base_url" in content

    def test_increments_layout_version(self):
        content = open(BOOTSTRAP_SCRIPT).read()
        assert "current_version + 1" in content

    def test_uses_secrets_context(self):
        content = open(BOOTSTRAP_SCRIPT).read()
        assert "secrets_context" in content
