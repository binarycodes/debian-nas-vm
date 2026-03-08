"""Shared fixtures for MVP1 tests."""
import copy
import os
import tempfile

import pytest
import yaml

SERVICES_PATH = os.path.join(os.path.dirname(__file__), "..", "nas_root", "var", "lib", "cloudyhome", "nas", "services.yml")
SECRETS_EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "nas_root", "var", "lib", "cloudyhome", "nas", "secrets.enc.yaml")
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "nas_root", "etc", "cloudyhome", "templates")


@pytest.fixture
def services_raw():
    with open(SERVICES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def secrets_raw():
    with open(SECRETS_EXAMPLE_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def valid_config(services_raw):
    from cloudyhome.models import NasConfig
    return NasConfig(**services_raw)


@pytest.fixture
def template_dir():
    return TEMPLATE_DIR
