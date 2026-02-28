.PHONY: setup install run run-ai extract-metadata clean

# Python executable to use
PYTHON = python3
VENV = .venv
VENV_BIN = $(VENV)/bin

setup:
	@echo "Setting up virtual environment..."
	$(PYTHON) -m venv $(VENV)
	@echo "Virtual environment created at $(VENV)."
	@echo "Run 'make install' to install dependencies."

install:
	@echo "Installing dependencies..."
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -r requirements.txt
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env file. Please add your API keys."; fi

run:
	@echo "Running SemanticFlow Evaluator (Claude deterministic metadata)..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --model claude

run-gemini:
	@echo "Running SemanticFlow Evaluator (Gemini deterministic metadata)..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --model gemini

run-ai:
	@echo "Running SemanticFlow Evaluator with AI Metadata Extraction (Claude)..."
	$(VENV_BIN)/python -m src.main --input data/courses --output data/output --config config/rubrics.yaml --ai --model claude

extract-metadata:
	@if [ -z "$(PDF)" ]; then echo "Usage: make extract-metadata PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@if [ -z "$(OUT)" ]; then echo "Usage: make extract-metadata PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@echo "Extracting metadata from $(PDF) to $(OUT)..."
	$(VENV_BIN)/python -m src.metadata --pdf $(PDF) --output $(OUT)

extract-metadata-ai:
	@if [ -z "$(PDF)" ]; then echo "Usage: make extract-metadata-ai PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@if [ -z "$(OUT)" ]; then echo "Usage: make extract-metadata-ai PDF=data/courses/your_file.pdf OUT=data/courses/your_file.json"; exit 1; fi
	@echo "Extracting metadata with AI from $(PDF) to $(OUT)..."
	$(VENV_BIN)/python -m src.metadata --pdf $(PDF) --output $(OUT) --ai

clean:
	@echo "Cleaning up generated cache and output files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
