PYTHON ?= python3
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PLAYWRIGHT := $(VENV)/bin/playwright
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: help setup dev api test generate clean

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: $(VENV)/bin/activate ## Create the environment and install dependencies and Chromium
	$(PIP) install -r requirements.txt
	$(PLAYWRIGHT) install chromium

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

dev: setup ## Start the API with automatic reload
	$(UVICORN) api:app --host $(HOST) --port $(PORT) --reload

api: setup ## Start the API without automatic reload
	$(UVICORN) api:app --host $(HOST) --port $(PORT)

test: setup ## Run all tests
	$(VENV_PYTHON) -m unittest -v

generate: setup ## Open the interactive CLI to generate a video
	$(VENV_PYTHON) video_maker.py

clean: ## Remove only Python caches and build artifacts
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -not -path './.venv/*' -delete
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov
