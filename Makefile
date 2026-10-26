PYTHON ?= python3
VENV ?= .venv
RUN_PY := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,$(PYTHON))

.PHONY: help setup check preview paper testnet live test status logs backup backtest performance

help:
	@echo "BTC Options Bot"
	@echo "  make setup    - create .env and local virtualenv"
	@echo "  make check    - validate credentials, market data, EMA, and option selection"
	@echo "  make preview  - show today's signal and exact planned order without placing it"
	@echo "  make paper    - run in paper mode (no real orders)"
	@echo "  make testnet  - run against Delta demo/testnet credentials"
	@echo "  make live     - run with REAL money (requires confirmation in .env)"
	@echo "  make test     - run the full test suite"
	@echo "  make status   - show persisted bot/runtime state"
	@echo "  make backtest - run the 7-cycle historical model backtest"
	@echo "  make performance - generate the full A-Z performance report"

setup:
	@$(PYTHON) -m venv $(VENV)
	@$(VENV)/bin/python -m pip install --upgrade pip
	@$(VENV)/bin/python -m pip install -e .
	@$(VENV)/bin/python -m btc_options_bot init

check:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot check

preview:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot preview

paper:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot run --mode paper

testnet:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot run --mode testnet

live:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot run --mode live

status:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot status

logs:
	@tail -n 100 -f logs/bot.log

test:
	@PYTHONPATH=src $(RUN_PY) -m unittest discover -s tests -v

backup:
	@PYTHONPATH=src $(RUN_PY) -m btc_options_bot backup

backtest:
	@$(RUN_PY) research/backtest.py

performance:
	@$(RUN_PY) research/performance.py
