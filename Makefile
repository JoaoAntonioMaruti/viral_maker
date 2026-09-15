PYTHON ?= python3
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PLAYWRIGHT := $(VENV)/bin/playwright
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: help setup dev api test generate clean

help: ## Mostra os comandos disponíveis
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: $(VENV)/bin/activate ## Cria o ambiente, instala dependências e o Chromium
	$(PIP) install -r requirements.txt
	$(PLAYWRIGHT) install chromium

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

dev: setup ## Inicia a API com recarregamento automático
	$(UVICORN) api:app --host $(HOST) --port $(PORT) --reload

api: setup ## Inicia a API sem recarregamento
	$(UVICORN) api:app --host $(HOST) --port $(PORT)

test: setup ## Executa todos os testes
	$(VENV_PYTHON) -m unittest -v

generate: setup ## Abre a CLI interativa para gerar um vídeo
	$(VENV_PYTHON) video_maker.py

clean: ## Remove apenas caches e artefatos de build Python
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -not -path './.venv/*' -delete
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov
