# Montandon Notebook Factory

A thin Django application that **indexes parameterized analysis notebooks, orchestrates their
execution, and publishes the results as static HTML** — the architecture proposed in
[monty-notebooks-plan](https://batpad.github.io/monty-notebooks-plan/).

* **Templates** (`AnalysisNotebook`) are Jupyter notebooks that declare their parameters as a JSON Schema.
* **Runs** (`NotebookRun`) execute a template for concrete parameters — an admin area, a hazard,
  a Montandon event — with [papermill](https://papermill.readthedocs.io/), render it with
  [MyST](https://mystmd.org/) and upload the static site to object storage (local disk, S3/MinIO or Azure Blob).
* A **browse UI** (Django templates + HTMX) and a **REST API** make every generated analysis
  discoverable by country, area, hazard, use case and date. Published pages are served straight from storage.
* A **Montandon poller** (Celery beat) watches STAC collections for new events and fires
  **trigger rules** that map events onto templates (Use Cases 2 and 3). Use Case 3 results are
  **written back** to Montandon as a `response-prioritisation` STAC collection.
* Users are **notified** (email + in-app) when a run they requested finishes.

Only light metadata lives in Postgres. Notebook content never touches the database.

```
 user / API ──▶ Django (index, orchestration) ──▶ Celery worker: papermill → MyST → upload ──▶ object storage ──▶ browser
                    ▲                                                    │
   cron poller ─────┘  Montandon STAC ◀── UC3 write-back ────────────────┘
```

## Quick start (no docker)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL with PostGIS,
GDAL/GEOS (`brew install gdal geos` — Homebrew paths are auto-detected), Node 18+ for MyST.

```sh
uv sync                                   # python deps
npm i -g mystmd                           # MyST >= 1.10 (widgets need it); or set MYST_COMMAND="npx -y mystmd@1.10.1"
cp .env.example .env                      # edit DATABASE_URL etc.

# database (Postgres.app on macOS ships PostGIS; on Linux install postgresql-postgis)
createdb notebook_factory && psql -d notebook_factory -c "CREATE EXTENSION postgis;"

uv run python manage.py migrate
uv run python manage.py seed_demo --countries NPL   # hazards, Nepal boundaries from GADM, templates, example rules
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://localhost:8000>. Log in, pick **Templates → Demo: area map → Run this analysis**,
choose an area on the map and hit *Run*. With no `CELERY_BROKER_URL` set, runs execute
**in-process** (a small thread pool, `NOTEBOOK_EAGER_WORKERS`) so nothing else needs to run.
Published pages are written to `data/published/` and served by Django at `/published/`.

Useful commands:

```sh
uv run python manage.py run_notebook demo-area-map --area NPL.1_1 -p label="Hi"   # run synchronously from the CLI
uv run python manage.py import_gadm VNM UKR --levels 0,1,2     # more boundaries (GADM 4.1)
uv run python manage.py import_admin_areas file.gpkg --level 2 --source cod   # COD / custom boundaries
uv run python manage.py sync_templates                          # reload notebook_templates/*/template.yml
uv run python manage.py ingest_event --file examples/events/nepal-earthquake.json   # fake an event, fire rules
uv run python manage.py poll_montandon                          # poll Montandon once (needs MONTANDON_API_TOKEN)
uv run python manage.py stale_runs --requeue                    # recover runs interrupted by a restart
make test lint
```

### With a real queue

Set `CELERY_BROKER_URL=redis://localhost:6379/0` and run, in separate shells:

```sh
uv run python manage.py runserver
uv run celery -A config worker -l info --concurrency 2
uv run celery -A config beat -l info          # Montandon poller + housekeeping
```

## Docker

```sh
docker compose up --build            # PostGIS, Redis, MinIO, web, worker, beat
docker compose run --rm web python manage.py seed_demo --countries NPL
docker compose run --rm web python manage.py createsuperuser
```

Web on <http://localhost:8000>, MinIO console on <http://localhost:9001> (minio / minio12345).
Published notebooks go to the `notebooks` bucket and are served from `http://localhost:9000/notebooks/…`.
Override host ports with `WEB_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`.

The same image runs `web`, `worker` and `beat` (see `docker/entrypoint.sh`), which maps directly
onto Kubernetes Deployments; configuration is entirely through environment variables.

## Configuration

Everything is in `.env.example`. The important knobs:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgis://…` (PostGIS is required for the map picker and boundary export) |
| `CELERY_BROKER_URL` | empty = in-process runs; Redis/RabbitMQ URL = real workers |
| `PUBLISHED_STORAGE_BACKEND` | `local`, `s3` (AWS, MinIO, …) or `azure` — plus the backend's credentials |
| `PUBLISHED_PUBLIC_BASE_URL` | optional CDN / public base for published files |
| `MYST_COMMAND`, `MYST_TEMPLATE` | how to invoke mystmd and which site theme to use |
| `MONTANDON_STAC_URL`, `MONTANDON_API_TOKEN` | Montandon STAC API (Bearer token from IFRC GO) |
| `MONTANDON_WRITEBACK_ENABLED` | allow UC3 runs to publish STAC collections back to Montandon |
| `NOTEBOOK_TEMPLATES_DIR`, `NOTEBOOK_WORK_DIR` | where templates live / where runs are executed |
| `EMAIL_*` | SMTP for notifications (console backend by default) |

## How a run works

1. `create_run()` validates the parameters against the template's JSON Schema, stores a `NotebookRun`
   (status `queued`) and enqueues `execute_run` (or hands it to the in-process pool).
2. **fetching** — the template is copied from the templates dir (or a git checkout), the target
   area and its subdivisions are exported as GeoJSON, the event's STAC item is written to `inputs/`.
3. **executing** — papermill runs the notebook with the user parameters plus the reserved ones
   (`area_geojson`, `subdivisions_geojson`, `event`, `output_dir`, …). Cell progress streams into the run log.
4. **rendering** — MyST builds a static site (`myst build --html`) with `BASE_URL` set to the run's
   final location. Code cells are collapsed (`MYST_HIDE_CODE`). The vendored
   [myst-anywidget-static-export](https://github.com/developmentseed/myst-anywidget-static-export)
   plugin turns anywidget outputs — lonboard maps, [manywidgets](https://github.com/developmentseed/manywidgets)
   charts, stats, toggles, legends and `jslink`ed controls — into kernel-free interactive widgets.
   Classic ipywidgets (e.g. `IntSlider`) are *not* rendered statically; use manywidgets instead.
5. **publishing** — the site, the executed `.ipynb` and anything the notebook wrote to `output_dir`
   are uploaded under `runs/<run id>/`. `output_url` points at `index.html`.
6. UC3 templates that wrote `output_dir/stac/collection.json` + `items/*.json` are pushed to Montandon.
7. Everyone interested (requester, `notify_emails`, rule recipients) gets an email and an in-app notification.

Runs can be fanned out over every area at an admin level in one request (a `RunBatch`), from the UI
(*Run for every area at this level*) or the API (`country` + `area_level`).

## Automation (Use Cases 2 & 3)

`EventTriggerRule` (manage it in the admin) says: *for items in these STAC collections, with these
roles / hazards / countries, run this template with these parameters*. Parameter values may use
Jinja, e.g. `{{ event.monty_corr_id }}` or `{{ props['monty:hazard_codes'][0] }}`. Rules run once per
event (`EventRunLink`) unless `rerun_on_update` is set, and can fan out over an admin level.

The `poll_montandon` beat task searches each watched collection since the last seen item datetime
(with overlap), upserts `Event` rows and fires matching rules. `seed_demo` creates two disabled example
rules. Webhooks from Montandon would replace only the poller; matching and run creation are shared.

## Writing a template

See [`notebook_templates/README.md`](notebook_templates/README.md). In short: a folder with
`template.yml` (title, use case, hazards, `parameters` JSON Schema, `requires_area`, `requires_event`,
`supports_writeback`) and a `notebook.ipynb` whose first code cell is tagged `parameters`. The bundled
templates (`demo-area-map`, `uc1-risk-exposure`, `uc2-impact-estimation`, `uc3-response-prioritisation`)
run end-to-end with **synthetic hazard/population data** so the pipeline can be exercised without
large downloads; swap the loaders for real rasters and the method stays the same.

## API

Browse the OpenAPI docs at `/api/docs/`. Highlights:

```
GET  /api/templates/                       templates with their parameter schema
GET  /api/runs/?country=NPL&hazard=flood   published runs (filters: notebook, use_case, area, event, status, since, until)
POST /api/runs/  {"notebook": "uc1-risk-exposure", "area": 12, "parameters": {...}}
POST /api/runs/  {"notebook": "...", "country": "NPL", "area_level": 2, "parameters": {...}}   # fan-out
POST /api/runs/<id>/cancel/  ·  POST /api/runs/<id>/retry/
GET  /api/areas/geojson/?country=NPL&level=2   simplified boundaries for the map picker
GET  /api/events/  ·  /api/trigger-rules/  ·  /api/batches/
```

Session or token auth (`rest_framework.authtoken`); reads are public, writes need a user.

## Layout

```
config/                 settings, celery, urls
factory/catalog/        Hazard, Country, AdminArea (PostGIS) + GADM/COD importers
factory/notebooks/      AnalysisNotebook, NotebookRun, RunBatch; parameter schemas; the pipeline
factory/events/         Event, EventTriggerRule, Montandon client, poller, write-back
factory/notifications/  Notification + delivery
factory/api/            DRF viewsets
factory/web/            HTMX views, templates, static
notebook_templates/     the notebook templates (one folder each)
examples/events/        sample STAC items for local testing
tests/                  pytest (needs the PostGIS database)
```

## Known sharp edges

* **In-process mode** is for laptops: runs live in threads of the dev server, so a restart interrupts
  them (`stale_runs --requeue` recovers). Use a broker + worker for anything shared.
* **Output size**: a MyST site is ~20–30 MB per run (theme bundles). Object storage is cheap, but a
  CDN in front of the bucket (`PUBLISHED_PUBLIC_BASE_URL`) is recommended.
* **Raster layers in static HTML** still need tiles served from somewhere; not addressed here.
* **Widgets**: only anywidget-based outputs render statically (lonboard, manywidgets, anywidget). The
  MyST theme's `thebe-core` bundles must stay in the output (`MYST_STRIP_THEBE=false`), and matplotlib
  figures need the inline backend (do not call `matplotlib.use("Agg")` in a template).
* **Container quirks**: the image needs `procps` (MyST shells out to `ps`) and pins `cryptography<47`
  because newer wheels raise SIGILL on some arm64 VMs (Colima on Apple Silicon). Both are in place.
* **Montandon write-back** uses the STAC Transactions extension (`POST/PUT /collections`, `/items`);
  it is off by default and needs a token with write rights.
