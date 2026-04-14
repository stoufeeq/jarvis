.PHONY: help up infra down logs migrate seed lint test dev

help:
	@echo "Jarvis — available commands:"
	@echo "  make infra       Start only Postgres + Redis (recommended for dev)"
	@echo "  make up          Start all services including backend in Docker"
	@echo "  make dev         Run backend locally with hot reload (needs: make infra first)"
	@echo "  make down        Stop all Docker services"
	@echo "  make logs        Tail backend logs (Docker mode)"
	@echo "  make migrate     Run Alembic migrations"
	@echo "  make makemig m=  Create new migration (m=message)"
	@echo "  make seed        Seed dev database"
	@echo "  make lint        Run ruff + mypy"
	@echo "  make test        Run pytest"
	@echo "  make fe          Start Next.js dev server"

infra:
	docker-compose up -d postgres redis

up:
	docker-compose up -d

dev:
	cd backend && uvicorn app.main:app --reload --port 8002

down:
	docker-compose down

logs:
	docker-compose logs -f backend

migrate:
	cd backend && alembic upgrade head

makemig:
	cd backend && alembic revision --autogenerate -m "$(m)"

seed:
	cd backend && python scripts/seed.py

lint:
	cd backend && ruff check app && mypy app

test:
	cd backend && pytest tests/ -v

fe:
	cd frontend && npm run dev

install-backend:
	cd backend && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

setup: install-backend install-frontend
	cp -n .env.example .env || true
	@echo "Done. Edit .env then run: make up && make migrate"
