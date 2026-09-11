# Local development without docker. Requires: uv, PostGIS, Node (for mystmd).
.PHONY: setup db migrate seed run worker beat poll test lint fmt shell check

setup:            ## create venv, install deps, install mystmd
	uv sync
	@command -v myst >/dev/null 2>&1 || npm i -g mystmd@1.10.1
	@test -f .env || cp .env.example .env
	@echo "Now create the database (see README) and run: make migrate seed run"

db:               ## create the postgres database + postgis extension (Postgres.app / local postgres)
	createdb notebook_factory 2>/dev/null || true
	psql -d notebook_factory -c "CREATE EXTENSION IF NOT EXISTS postgis;"

migrate:
	uv run python manage.py migrate

seed:             ## hazards, Nepal boundaries (GADM), templates, example rules
	uv run python manage.py seed_demo --countries NPL

superuser:
	uv run python manage.py createsuperuser

run:              ## dev server (runs notebooks in-process when CELERY_BROKER_URL is empty)
	uv run python manage.py runserver

worker:           ## celery worker (needs CELERY_BROKER_URL)
	uv run celery -A config worker -l info --concurrency 2

beat:             ## celery beat: Montandon poller + cleanup (needs CELERY_BROKER_URL)
	uv run celery -A config beat -l info

poll:             ## poll Montandon once
	uv run python manage.py poll_montandon

demo-run:         ## run the demo template synchronously
	uv run python manage.py run_notebook demo-area-map --area NPL.1_1 -p label="Hello Nepal"

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

shell:
	uv run python manage.py shell

check:
	uv run python manage.py check
