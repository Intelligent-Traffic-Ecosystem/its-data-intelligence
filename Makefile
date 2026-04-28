.PHONY: up down logs test test-integration test-all lint mock migrate

up:
	docker compose up -d

down:
	docker compose down -v

logs:
	docker compose logs -f

test:
	pytest tests/ -v --ignore=tests/integration

test-integration:
	pytest tests/integration/ -v --tb=short --timeout=120

test-all: test test-integration

lint:
	ruff check src/ tests/

mock:
	python tools/mock_producer.py --cameras 4 --rate 10

migrate:
	alembic upgrade head
