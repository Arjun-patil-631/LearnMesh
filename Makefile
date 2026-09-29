.PHONY: help install test test-cov lint format migrate dev docker-up docker-down

PYTHON ?= python
VENV_DIR ?= .venv

ifeq ($(OS),Windows_NT)
    VENV_BIN = $(VENV_DIR)\Scripts
    PY = $(VENV_BIN)\python.exe
    PYTEST = $(VENV_BIN)\pytest.exe
    ALEMBIC = $(VENV_BIN)\alembic.exe
else
    VENV_BIN = $(VENV_DIR)/bin
    PY = $(VENV_BIN)/python
    PYTEST = $(VENV_BIN)/pytest
    ALEMBIC = $(VENV_BIN)/alembic
endif

help:
	@echo "LearnMesh Platform Automation Commands:"
	@echo "  make install     Create venv and install dependencies"
	@echo "  make test        Run full test suite"
	@echo "  make lint        Run code style and typing checks"
	@echo "  make format      Auto-format code with black and ruff"
	@echo "  make migrate     Run database migrations with Alembic"
	@echo "  make dev         Run development server"
	@echo "  make docker-up   Spin up full stack with docker-compose"
	@echo "  make docker-down Teardown docker containers"

install:
	$(PYTHON) -m venv $(VENV_DIR)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements.txt

test:
	$(PYTEST) backend/tests -v

lint:
	$(PY) -m ruff check backend/
	$(PY) -m mypy backend/ --ignore-missing-imports

format:
	$(PY) -m ruff format backend/

migrate:
	$(ALEMBIC) upgrade head

dev:
	$(PY) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
