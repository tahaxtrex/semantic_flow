.PHONY: setup install run run-gemini run-no-ai extract-metadata extract-metadata-no-ai clean

# Python executable to use
PYTHON = python3

# Detect virtual environment
ifeq ($(wildcard .venv),)
	# .venv doesn't exist, check if current python is in a conda/micromamba env
	PYTHON_PATH := $(shell which python3 2>/dev/null || which python 2>/dev/null)
	ifneq ($(PYTHON_PATH),)
		# Check if the python path contains /envs/ (indicator of conda/micromamba)
		ifneq ($(findstring /envs/,$(PYTHON_PATH)),)
			# Extract the environment directory (parent of bin directory)
			VENV := $(shell dirname $(shell dirname $(PYTHON_PATH)))
		else
			VENV := .venv
		endif
	else
		VENV := .venv
	endif
else
	VENV := .venv
endif

VENV_BIN := $(VENV)/bin

setup:
	@if [ -d ".venv" ]; then \
		echo "Virtual environment already exists at .venv"; \
	elif [ "$(VENV)" != ".venv" ]; then \
		echo "Detected existing conda/micromamba environment at $(VENV)"; \
		echo "Skipping virtual environment creation. Run 'make install' to install dependencies."; \
	else \
		echo "Setting up virtual environment..."; \
		$(PYTHON) -m venv $(VENV); \
		echo "Virtual environment created at $(VENV)."; \
		echo "Run 'make install' to install dependencies."; \
	fi

install:
	@echo "Installing dependencies..."
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -r requirements.txt
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env file. Please add your API keys."; fi

run:
	@echo "Running SemanticFlow Evaluator — Claude evaluation, two-phase AI metadata (heuristic + Gemini/Claude)..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --ai --model claude

run-gemini:
	@echo "Running SemanticFlow Evaluator — Gemini evaluation, two-phase AI metadata..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --ai --model gemini

run-no-ai:
	@echo "Running SemanticFlow Evaluator — Claude evaluation, heuristic-only metadata (no LLM)..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --model claude

# Standalone metadata extraction (two-phase: heuristic + Gemini/Claude LLM enrichment)
# AI enrichment is ON by default. Pass --no-ai to run heuristic phase only.
extract-metadata:
	@if [ -z "$(PDF)" ]; then echo "Usage: make extract-metadata PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@if [ -z "$(OUT)" ]; then echo "Usage: make extract-metadata PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@echo "Extracting metadata (heuristic + AI) from $(PDF) to $(OUT)..."
	$(VENV_BIN)/python -m src.metadata --pdf $(PDF) --output $(OUT)

extract-metadata-no-ai:
	@if [ -z "$(PDF)" ]; then echo "Usage: make extract-metadata-no-ai PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@if [ -z "$(OUT)" ]; then echo "Usage: make extract-metadata-no-ai PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@echo "Extracting metadata (heuristic only, no LLM) from $(PDF) to $(OUT)..."
	$(VENV_BIN)/python -m src.metadata --pdf $(PDF) --output $(OUT) --no-ai

clean:
	@echo "Cleaning up generated cache and output files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
