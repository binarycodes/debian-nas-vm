.PHONY: test deps venv

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV):
	python3 -m venv $(VENV)

deps: $(VENV)
	$(PIP) install -q pydantic pyyaml jinja2 tomli-w pytest

test: deps
	$(PYTHON) -m pytest . -v
