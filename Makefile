SHELL := /bin/bash
PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help setup demo test lint data translate clean

help:
	@echo "setup      python venv + deps"
	@echo "demo       translate 12 sample tables, migrate to DuckDB, write reports/reconciliation.md"
	@echo "test       pytest: type mapping, DDL translation, reconciliation edge cases"
	@echo "lint       ruff check + format check"
	@echo "data       regenerate the synthetic sample data in samples/data"
	@echo "clean      remove venv, build/ and reports/"

$(BIN)/activate: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[dev]"
	touch $(BIN)/activate

setup: $(BIN)/activate

# PRODUCT_RETURNS loses one Parquet file on purpose: the report must show it
# as FAIL while the other 11 tables PASS.
demo: setup
	$(BIN)/migrate demo --simulate-lost-file PRODUCT_RETURNS
	@echo "report: reports/reconciliation.md"

translate: setup
	$(BIN)/migrate translate

test: setup
	$(BIN)/pytest

lint: setup
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

data: setup
	$(BIN)/migrate generate --seed 42

clean:
	rm -rf $(VENV) build reports .pytest_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
