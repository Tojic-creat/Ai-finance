# Makefile for FinAssist (Django backend + ml_service)
# Usage: make <target>
SHELL := /bin/bash

# === Configurable variables ===
PYTHON ?= python3
VENV ?= .venv
ACTIVATE := $(VENV)/bin/activate

BACKEND_DIR ?= backend
ML_DIR ?= ml_service

COMPOSE ?= docker compose
DOCKER_BUILD ?= docker build

# default image tag (short git sha if available)
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo local)
IMAGE_TAG ?= $(GIT_SHA)

# === Phony targets ===
.PHONY: help venv install install-backend install-ml run-local-backend \
        up down logs build build-images push-images migrate makemigrations \
        collectstatic createsuperuser test-backend test-ml lint format ci \
        shell-backend shell-ml stop restart clean

# === Help ===
help:
	@echo "Makefile targets for FinAssist"
	@echo
	@echo "  make venv                  # create python venv (if missing)"
	@echo "  make install-backend       # install backend requirements into venv"
	@echo "  make install-ml            # install ml_service requirements into venv"
	@echo "  make run-local-backend     # run backend devserver locally (venv)"
	@echo "  make up                    # docker compose up (dev)"
	@echo "  make down                  # docker compose down"
	@echo "  make logs                  # follow docker compose logs"
	@echo "  make migrate               # run django migrations (docker)"
	@echo "  make makemigrations        # create django migrations (docker)"
	@echo "  make collectstatic         # collect static files (docker)"
	@echo "  make createsuperuser       # interactive createsuperuser (docker)"
	@echo "  make test-backend          # run backend tests"
	@echo "  make test-ml               # run ml_service tests"
	@echo "  make lint                  # run linters (black/isort/flake8/mypy)"
	@echo "  make format                # autoformat (black/isort)"
	@echo "  make build-images          # docker build images for backend & ml_service"
	@echo "  make build                 # alias for build-images"
	@echo "  make ci                    # run lint + tests (CI-like)"
	@echo "  make clean                 # remove pyc, __pycache__, .pytest_cache"
	@echo

# === Virtual environment & installs ===
venv:
	@if [ ! -d "$(VENV)" ]; then \
		$(PYTHON) -m venv $(VENV); \
		echo "Created virtualenv at $(VENV). Activate with: source $(ACTIVATE)"; \
	else \
		echo "Virtualenv $(VENV) already exists"; \
	fi

install-backend: venv
	@echo "Installing backend dependencies into venv..."
	@bash -lc 'source $(ACTIVATE) && pip install --upgrade pip'
	@bash -lc 'if [ -f "$(BACKEND_DIR)/requirements-dev.txt" ]; then \
		source $(ACTIVATE) && pip install -r $(BACKEND_DIR)/requirements-dev.txt; \
		elif [ -f "$(BACKEND_DIR)/requirements.txt" ]; then \
		source $(ACTIVATE) && pip install -r $(BACKEND_DIR)/requirements.txt; \
		else echo "No requirements in $(BACKEND_DIR) found."; fi'

install-ml: venv
	@echo "Installing ml_service dependencies into venv..."
	@bash -lc 'source $(ACTIVATE) && pip install --upgrade pip'
	@bash -lc 'if [ -f "$(ML_DIR)/requirements-ml.txt" ]; then \
		source $(ACTIVATE) && pip install -r $(ML_DIR)/requirements-ml.txt; \
		elif [ -f "$(ML_DIR)/requirements.txt" ]; then \
		source $(ACTIVATE) && pip install -r $(ML_DIR)/requirements.txt; \
		else echo "No requirements in $(ML_DIR) found."; fi'

install: install-backend install-ml

# === Run / Docker Compose ===
up:
	@echo "Starting services with '$(COMPOSE) up -d'..."
	$(COMPOSE) up -d --build

down:
	@echo "Stopping services..."
	$(COMPOSE) down

logs:
	@echo "Tailing logs (press Ctrl+C to exit)..."
	$(COMPOSE) logs -f

restart:
	@$(COMPOSE) restart

stop:
	@$(COMPOSE) stop

# === Django management (via docker compose) ===
# These assume service name 'backend' or 'web' in your docker-compose.yml.
_mgmt_exec = $(COMPOSE) exec -T backend

migrate:
	@echo "Applying migrations..."
	@$(call _mgmt_exec) python manage.py migrate --noinput

makemigrations:
	@echo "Making migrations (interactive output)..."
	@$(call _mgmt_exec) python manage.py makemigrations

collectstatic:
	@echo "Collect static files..."
	@$(call _mgmt_exec) python manage.py collectstatic --noinput

createsuperuser:
	@echo "Creating superuser (interactive)..."
	@$(call _mgmt_exec) python manage.py createsuperuser

shell-backend:
	@$(call _mgmt_exec) python manage.py shell

shell-ml:
	@$(COMPOSE) exec -T ml_service /bin/bash

# === Tests ===
test-backend:
	@echo "Running backend tests..."
	@if [ -d "$(BACKEND_DIR)" ]; then \
		if [ -f "$(VENV)/bin/activate" ]; then \
			bash -lc 'source $(ACTIVATE) && cd $(BACKEND_DIR) && if command -v pytest >/dev/null 2>&1; then pytest -q --maxfail=1; else python manage.py test; fi'; \
		else \
			echo "No venv found. Running inside docker-compose (if available)..."; \
			$(COMPOSE) run --rm backend bash -lc 'cd $(BACKEND_DIR) && if command -v pytest >/dev/null 2>&1; then pytest -q --maxfail=1; else python manage.py test; fi'; \
		fi \
	else \
		echo "Backend directory '$(BACKEND_DIR)' not found."; \
	fi

test-ml:
	@echo "Running ml_service tests..."
	@if [ -d "$(ML_DIR)" ]; then \
		if [ -f "$(VENV)/bin/activate" ]; then \
			bash -lc 'source $(ACTIVATE) && cd $(ML_DIR) && if command -v pytest >/dev/null 2>&1; then pytest -q --maxfail=1; else echo "pytest not installed in venv"; fi'; \
		else \
			$(COMPOSE) run --rm ml_service bash -lc 'cd $(ML_DIR) && if command -v pytest >/dev/null 2>&1; then pytest -q --maxfail=1; else echo "pytest not available in container"; fi'; \
		fi \
	else \
		echo "ML service directory '$(ML_DIR)' not found."; \
	fi

# === Linters & formatters ===
lint:
	@echo "Running linters (black/isort/flake8/mypy)..."
	@bash -lc 'if command -v black >/dev/null 2>&1; then black --check .; else echo "black not installed (install via make install-backend or pip)"; fi'
	@bash -lc 'if command -v isort >/dev/null 2>&1; then isort --check-only .; else echo "isort not installed"; fi'
	@bash -lc 'if command -v flake8 >/dev/null 2>&1; then flake8 .; else echo "flake8 not installed"; fi'
	@bash -lc 'if command -v mypy >/dev/null 2>&1; then mypy . || true; else echo "mypy not installed"; fi'

format:
	@echo "Formatting code (black/isort)..."
	@bash -lc 'if command -v black >/dev/null 2>&1; then black .; else echo "black not installed"; fi'
	@bash -lc 'if command -v isort >/dev/null 2>&1; then isort .; else echo "isort not installed"; fi'

# === Docker build images ===
build-images:
	@echo "Building backend image..."
	@$(DOCKER_BUILD) -t $(IMAGE_TAG)-backend $(BACKEND_DIR)
	@echo "Building ml_service image..."
	@$(DOCKER_BUILD) -t $(IMAGE_TAG)-ml $(ML_DIR)

build: build-images

# Optional push (you can customize registry/tagging)
push-images:
	@echo "Push images: customize this target to push to your registry (GHCR/DockerHub)."
	@echo "Example: docker tag $(IMAGE_TAG)-backend ghcr.io/owner/repo-backend:$(IMAGE_TAG) && docker push ..."

# === CI-like target ===
ci: lint test-backend test-ml
	@echo "CI tasks finished."

# === Clean ===
clean:
	@echo "Cleaning python artifacts..."
	@find . -type d -name "__pycache__" -print0 | xargs -0 rm -rf || true
	@find . -type f -name "*.pyc" -print0 | xargs -0 rm -f || true
	@rm -rf .pytest_cache || true

