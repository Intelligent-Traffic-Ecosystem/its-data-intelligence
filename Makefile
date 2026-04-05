.PHONY: up down logs test lint mock migrate

up:
	docker compose up -d

down:
	docker compose down -v

logs:
	docker compose logs -f

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

mock:
	python tools/mock_producer.py --cameras 4 --rate 10

migrate:
	alembic upgrade head
