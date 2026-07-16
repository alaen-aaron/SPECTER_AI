.PHONY: help up down logs restart build ps \
        lint format test test-backend lint-backend format-backend \
        lint-frontend format-frontend \
        shell-api shell-db migrate makemigration

help:
	@echo "SPECTER_AI — common developer commands"
	@echo ""
	@echo "  make up              Start the full stack (docker compose up --build)"
	@echo "  make down            Stop the stack and remove containers"
	@echo "  make logs            Tail logs from every service"
	@echo "  make restart         Restart all services"
	@echo "  make ps              Show running service status"
	@echo ""
	@echo "  make lint            Lint backend + frontend"
	@echo "  make format          Format backend + frontend"
	@echo "  make test            Run backend test suite"
	@echo ""
	@echo "  make shell-api       Open a shell inside the api container"
	@echo "  make shell-db        Open a psql shell inside postgres"
	@echo "  make migrate         Apply alembic migrations"
	@echo "  make makemigration m=\"message\"   Autogenerate a new migration"

COMPOSE = docker compose -f infra/docker-compose.yml

up:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

restart:
	$(COMPOSE) restart

build:
	$(COMPOSE) build

ps:
	$(COMPOSE) ps

# --- Backend -----------------------------------------------------------
lint-backend:
	cd backend && ruff check . && mypy app

format-backend:
	cd backend && black . && ruff check --fix .

test-backend:
	cd backend && pytest

# --- Frontend -----------------------------------------------------------
lint-frontend:
	cd frontend && npm run lint

format-frontend:
	cd frontend && npm run format

lint: lint-backend lint-frontend

format: format-backend format-frontend

test: test-backend

# --- Database -----------------------------------------------------------
shell-api:
	$(COMPOSE) exec api /bin/bash

shell-db:
	$(COMPOSE) exec postgres psql -U specter -d specter

migrate:
	$(COMPOSE) exec api alembic upgrade head

makemigration:
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"
