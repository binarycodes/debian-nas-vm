.PHONY: test deps venv

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV):
	python3 -m venv $(VENV)

deps: $(VENV)
	$(PIP) install -q pytest
	$(PIP) install -q -e ../nas_root/usr/local/lib/cloudyhome

test: deps
	$(PYTHON) -m pytest . -v
