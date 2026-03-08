.PHONY: validate lint test deps

VENV    := .venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
RUFF    := $(VENV)/bin/ruff
BANDIT  := $(VENV)/bin/bandit

PACKAGE := ../nas_root/usr/local/lib/cloudyhome
SCRIPTS := \
    ../nas_root/usr/local/sbin/nas-apply-config \
    ../nas_root/usr/local/sbin/nas-garage-bootstrap \
    ../nas_root/usr/local/sbin/nas-render-config \
    ../nas_root/usr/local/sbin/nas-validate-config
BASH_SCRIPTS := \
    ../nas_root/usr/local/sbin/nas-health-alert \
    ../nas_root/usr/local/sbin/nas-zedlet-wrapper \
    ../nas_root/usr/local/sbin/nas-zfs-import

$(VENV):
	python3 -m venv $(VENV)

deps: $(VENV)
	$(PIP) install -q pytest ruff bandit
	$(PIP) install -q -e $(PACKAGE)

lint: deps
	$(RUFF) check $(PACKAGE) $(SCRIPTS)
	$(BANDIT) -q -c bandit.yaml -r $(PACKAGE) $(SCRIPTS)
	shellcheck $(BASH_SCRIPTS)

test: deps
	$(PYTHON) -m pytest . -v

validate: lint test
