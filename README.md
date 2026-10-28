**Note**: This is currently a "vibe-coded" experiment - the below README and most of the code was AI-generated.

# Montandon Notebook Factory

A thin Django application that **indexes parameterized analysis notebooks, orchestrates their
execution, and publishes the results as static HTML** — the architecture proposed in
[monty-notebooks-plan](https://batpad.github.io/monty-notebooks-plan/).

* **Templates** (`AnalysisNotebook`) are Jupyter notebooks that declare their parameters as a JSON Schema.
* **Runs** (`NotebookRun`) execute a template for concrete parameters — an admin area, a STAC item from
  any configured catalog, filter tags — with [papermill](https://papermill.readthedocs.io/), render it with
  [MyST](https://mystmd.org/) and upload the static site to object storage (local disk, S3/MinIO or Azure Blob).
* A **browse UI** (Django templates + HTMX) and a **REST API** make every generated analysis
  discoverable by country, area, date and any **filter**. Published pages are served straight from storage.
* **Filters** (`Facet` + `FacetOption`) are staff-defined dimensions such as *Hazard* and *Use case*
  (both seeded), or anything else (*Sector*, *Programme*…). Templates, runs, STAC items and trigger rules
  are tagged with options; see [Filters](#filters).
* **STAC catalogs** (`StacCatalog`) are any STAC APIs staff register in the UI (URL + optional API key).
  Users pick catalog → collection → item when starting a run. Montandon is one such catalog, with a
  `montandon` profile that also reads the `monty:*` fields (correlation id, countries); see
  [STAC catalogs](#stac-catalogs).
* A **poller** (Celery beat) watches catalog collections for new items and fires **trigger rules**
  that map them onto templates (Use Cases 2 and 3). Use Case 3 results are **written back** to the
  item's catalog (e.g. Montandon) as a `response-prioritisation` STAC collection.
* Users are **notified** (email + in-app) when a run they requested finishes.

Only light metadata lives in Postgres. Notebook content never touches the database.

```
 user / API ──▶ Django (index, orchestration) ──▶ Celery worker: papermill → MyST → upload ──▶ object storage ──▶ browser
                    ▲                                                    │
   cron poller ─────┘  STAC catalogs  ◀── UC3 write-back ────────────────┘
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
uv run python manage.py seed_demo --countries NPL   # hazard/use case filters, Nepal boundaries from GADM, templates, example rules
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://localhost:8000>. Log in, pick **Templates → Demo: area map → Run this analysis**,
choose an area on the map and hit *Run*.

The top bar also links to:

* **STAC items** (`/items/`): items picked for runs or ingested by the poller.
* **Filters** (`/filters/`, staff only): the filters runs are tagged and searched by (Hazard and Use case
  are seeded).
* **Catalogs** (`/catalogs/`, staff only): the STAC APIs runs can take items from. `seed_demo` adds Montandon (staging);
  it needs `MONTANDON_API_TOKEN` (or a key set on the Catalogs page) to list collections. To try a public
  catalog, add e.g. Earth Search (`https://earth-search.aws.element84.com/v1`, no key) and hit *Test connection*.

With no `CELERY_BROKER_URL` set, runs execute **in-process** (a small thread pool,
`NOTEBOOK_EAGER_WORKERS`) so nothing else needs to run.
Published pages are written to `data/published/` and served by Django at `/published/`.

Useful commands:

```sh
uv run python manage.py run_notebook demo-area-map --area NPL.1_1 -p label="Hi"   # run synchronously from the CLI
uv run python manage.py import_gadm VNM UKR --levels 0,1,2     # more boundaries (GADM 4.1)
uv run python manage.py import_admin_areas file.gpkg --level 2 --source cod   # COD / custom boundaries
uv run python manage.py sync_templates                          # import the example templates in notebook_templates/
uv run python manage.py ingest_item --catalog montandon --file examples/stac_items/nepal-earthquake.json   # fake an item, fire rules
uv run python manage.py poll_catalogs                           # poll every catalog with enabled rules once
uv run python manage.py run_notebook uc2-impact-estimation --area NPL --catalog montandon --collection reference-events --item <id>
uv run python manage.py stale_runs --requeue                    # recover runs interrupted by a restart
make test lint
```

### With a real queue

Set `CELERY_BROKER_URL=redis://localhost:6379/0` and run, in separate shells:

```sh
uv run python manage.py runserver
uv run celery -A config worker -l info --concurrency 2
uv run celery -A config beat -l info          # STAC poller + housekeeping
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

The same image runs `web`, `worker` and `beat` (see `docker/entrypoint.sh`); configuration is
entirely through environment variables.

## Kubernetes

[`helm/`](helm/) holds a chart that runs `web`, `worker`, `beat`, a Redis broker and an nginx proxy
that serves published runs from the bucket under `/published/`. PostGIS and object storage are
external. Every push to `main` publishes the image to `ghcr.io/developmentseed/notebook-factory` and
the chart to `oci://ghcr.io/developmentseed/charts`; IFRC deploys it with ArgoCD from
[go-deploy](https://github.com/IFRCGo/go-deploy). [`helm/README.md`](helm/README.md) has the
architecture diagram and values; [`deploy/ovh/`](deploy/ovh/) is a test deployment.

## Configuration

Everything is in `.env.example`. The important knobs:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgis://…` (PostGIS is required for the map picker and boundary export) |
| `CELERY_BROKER_URL` | empty = in-process runs; Redis/RabbitMQ URL = real workers |
| `PUBLISHED_STORAGE_BACKEND` | `local`, `s3` (AWS, MinIO, …) or `azure` — plus the backend's credentials |
| `PUBLISHED_PUBLIC_BASE_URL` | optional CDN / public base for published files |
| `MYST_COMMAND`, `MYST_TEMPLATE` | how to invoke mystmd and which site theme to use |
| `STAC_TIMEOUT` | HTTP timeout (s) for every STAC catalog (default 60) |
| `STAC_POLL_INTERVAL_SECONDS`, `STAC_POLL_LOOKBACK_DAYS`, `STAC_POLL_OVERLAP_MINUTES`, `STAC_POLL_PAGE_SIZE` | poller schedule and search window |
| `STAC_COLLECTIONS_CACHE_SECONDS` | how long the run form caches a catalog's collection list (default 600) |
| `MONTANDON_STAC_URL`, `MONTANDON_API_TOKEN`, `MONTANDON_WRITEBACK_ENABLED` | only used by `seed_demo` (and the one-off data migration) to create the Montandon catalog |
| `NOTEBOOK_TEMPLATES_DIR`, `NOTEBOOK_WORK_DIR` | example templates for `sync_templates` / where runs are executed |
| `NOTEBOOK_TEMPLATE_MAX_BYTES`, `NOTEBOOK_TEMPLATE_CACHE_DIR` | uploaded template size limit / where workers unpack template bundles |
| `EMAIL_*` | SMTP for notifications (console backend by default) |

The older `MONTANDON_TIMEOUT` / `MONTANDON_POLL_*` names are still read when the `STAC_*` ones are unset.
STAC catalogs themselves (URL, API key, profile, write-back) and filters are not configured through the
environment: staff manage them in the UI (see [STAC catalogs](#stac-catalogs) and [Filters](#filters)).

## How a run works

1. `create_run()` validates the parameters against the template's JSON Schema, stores a `NotebookRun`
   (status `queued`), tags it with filter options (see [Filters](#filters)) and enqueues `execute_run` (or hands it to the in-process pool).
2. **fetching** — the template is copied from the templates dir (or a git checkout), the target
   area and its subdivisions are exported as GeoJSON, the run's STAC item is written to `inputs/`.
3. **executing** — papermill runs the notebook with the user parameters plus the reserved ones
   (`area_geojson`, `subdivisions_geojson`, `stac_item`, `stac_api_url`, `filters`, `output_dir`, …). The kernel gets the
   item's catalog as `STAC_API_URL` / `STAC_API_KEY` env vars. Cell progress streams into the run log.
4. **rendering** — MyST builds a static site (`myst build --html`) with `BASE_URL` set to the run's
   final location. Code cells are collapsed (`MYST_HIDE_CODE`). The vendored
   [myst-anywidget-static-export](https://github.com/developmentseed/myst-anywidget-static-export)
   plugin turns anywidget outputs — lonboard maps, [manywidgets](https://github.com/developmentseed/manywidgets)
   charts, stats, toggles, legends and `jslink`ed controls — into kernel-free interactive widgets.
   Classic ipywidgets (e.g. `IntSlider`) are *not* rendered statically; use manywidgets instead.
5. **publishing** — the site, the executed `.ipynb` and anything the notebook wrote to `output_dir`
   are uploaded under `runs/<run id>/`. `output_url` points at `index.html`.
6. UC3 templates that wrote `output_dir/stac/collection.json` + `items/*.json` are pushed to the item's
   catalog, if that catalog has write-back enabled.
7. Everyone interested (requester, `notify_emails`, rule recipients) gets an email and an in-app notification.

Runs can be fanned out over every area at an admin level in one request (a `RunBatch`), from the UI
(*Run for every area at this level*) or the API (`country` + `area_level`).

## Uploading notebooks

Staff can publish notebooks that were executed elsewhere under **Upload** (or `POST /api/runs/upload/`).
An upload is a `NotebookRun` without a template (`trigger=upload`): it gets a title, summary, description,
an optional area and STAC item, and filter tags, and then shows up in browse and the API like any other run
(browse's *Source → Uploaded notebooks* lists only uploads).

* Upload a `.zip` with the notebook and the files it links to (images, data), or a bare `.ipynb`. If the
  zip holds several notebooks, pick the one to publish. Files next to it are published with it.
* Nothing is executed. The pipeline unpacks the archive, renders it with MyST and publishes it
  (queued → unpacking → rendering → publishing). Unexecuted code cells only produce a warning.
* The original archive is kept as `runs/<run id>/source.zip`. *Retry* re-renders from it, which is also
  how to pick up a new MyST theme.
* Filter tags come from the form (manual), then the STAC item. There is no template or parameter source.
* Limits: `NOTEBOOK_UPLOAD_MAX_BYTES` (200 MB), `NOTEBOOK_UPLOAD_MAX_UNCOMPRESSED_BYTES` (1 GB) and
  `NOTEBOOK_UPLOAD_MAX_FILES` (2000). Paths outside the archive and symlinks are refused. Behind
  ingress-nginx, raise `proxy-body-size` too (see `helm/values.yaml`).

## STAC catalogs

A `StacCatalog` is any STAC API. Staff add catalogs on the **Catalogs** page (with a *Test connection*
button), in the admin, or with `POST /api/catalogs/`:

* **API key**: optional, sent as `Authorization: Bearer <key>`. It is stored in plain text, never shown
  back in the UI and write-only in the API.
* **Profile**: `generic` reads core STAC properties only. `montandon` also reads `monty:corr_id`
  (shown as the correlation id) and `monty:country_codes` (used by trigger-rule country filters, by
  rules to pick areas, and to preselect the country when a run is started from an item's page).
* **Write-back**: off by default. When on, UC3-style results are pushed to this catalog (see below).

**Picking an item.** Templates with `requires_stac_item` get a picker in the run form: catalog →
collection → item (the newest 25, or *Find by item id*). The chosen item is fetched live and stored as
a `StacItem`, so it does not need to have come through the poller. A template's `stac_hints` narrow
and preselect the lists (e.g. uc2/uc3 suggest Montandon's `reference-events`); *Show all catalogs &
collections* lifts them, and the API / CLI ignore them. The **STAC items** page lists every stored item,
filterable by catalog and by filters with a STAC property.

**In the notebook.** The run gets `stac_item` (the raw item), `stac_item_json`, `stac_collection`,
`stac_api_url` and `stac_catalog_profile` as parameters. The kernel gets `STAC_API_URL` and
`STAC_API_KEY` as environment variables, set per run so parallel runs never see each other's keys; the
key never appears in the published notebook. See
[`notebook_templates/README.md`](notebook_templates/README.md#using-the-stac-item).

**Write-back.** A template with `supports_writeback` writes `output_dir/stac/collection.json` and
`output_dir/stac/items/*.json`; after publishing they are pushed (STAC Transactions) to the catalog
the run's item came from, only if that catalog has write-back enabled. Otherwise the run log says it
was skipped.

## Filters

Staff manage filters under **Filters** in the UI (or the admin, or `/api/filters/`). Each filter has
options, may allow several options per run (`multiple`), and may name a STAC property
(`stac_property`, e.g. `monty:hazard_codes`) whose values map onto options via their *match codes*
(prefixes). A run's options for each filter come from the first source that has any:

1. **manual**: picked in the run form's *Tags*, edited later on the run page (requester or staff), or
   `filters` in `POST /api/runs/`;
2. **a template parameter**: one whose name is the filter key (e.g. `hazard`), or that declares
   `x-filter: <key>`;
3. **the STAC item**: options matched from the filter's `stac_property`, for items from any catalog;
4. **the template**: when `template.yml` `filters:` lists exactly one option for that filter.

Option keys are a controlled vocabulary: a parameter value, `template.yml` entry or API value that is
not an existing option is skipped (with a log warning). Filter keys are used as query parameters, so
names the app already uses (`q`, `status`, `catalog`, `country`, …) are rejected. A filter cannot be
deleted while any run, template, item or rule uses it.

Browse, the STAC item list and the API filter with `?<filter key>=<option>` (repeat a key for "any of";
different keys must all match), e.g. `?hazard=flood&hazard=cyclone&use_case=uc2`. Notebooks receive
the run's tags as the `filters` parameter.

## Automation (Use Cases 2 & 3)

`TriggerRule` (manage it in the admin) says: *for items in these collections of this catalog, with these
roles / filter options / countries (and an optional CQL2 `search_filter`), run this template with these
parameters*. Country filters only match items from catalogs whose profile extracts them
(`montandon`). Parameter values may use Jinja, e.g. `{{ item.correlation_id }}`, `{{ filters.hazard[0] }}` or
`{{ props['monty:hazard_codes'][0] }}` (`event` still works as an alias of `item`). Rules run once per
item (`ItemRuleLink`) unless `rerun_on_update` is set, and can fan out over an admin level.

The `poll_catalogs` beat task searches each watched (catalog, collection) since the last seen item
datetime (with overlap), upserts `StacItem` rows and fires matching rules. `seed_demo` creates the
Montandon catalog and two disabled example rules. Webhooks would replace only the poller; matching and
run creation are shared.

## Writing a template

Upload templates in the app under **Templates → New template** (users with the *Can change analysis
notebook* permission). Parameters are detected from the `parameters` cell and edited with a live form preview, and each
change that affects runs is kept as a version that runs record (rollback from **Versions**). See
[`notebook_templates/README.md`](notebook_templates/README.md). In short: a folder with
`template.yml` (title, `filters` such as use case and hazards, `parameters` JSON Schema, `requires_area`, `requires_stac_item`,
`stac_hints`, `supports_writeback`) and a `notebook.ipynb` whose first code cell is tagged `parameters`. The bundled
templates (`demo-area-map`, `uc1-risk-exposure`, `uc2-impact-estimation`, `uc3-response-prioritisation`)
run end-to-end with **synthetic hazard/population data** so the pipeline can be exercised without
large downloads; swap the loaders for real rasters and the method stays the same.

## API

Browse the OpenAPI docs at `/api/docs/`. Highlights:

```
GET  /api/templates/                       templates with their parameter schema
GET  /api/runs/?country=NPL&hazard=flood   published runs (also: notebook, area, stac_item, catalog, status, since, until, any filter key)
GET  /api/filters/  ·  /api/filter-options/   filters and their options (staff can create / edit)
POST /api/runs/  {"notebook": "uc1-risk-exposure", "area": 12, "parameters": {...}}
POST /api/runs/  {"notebook": "...", "country": "NPL", "area_level": 2, "parameters": {...}}   # fan-out
POST /api/runs/  {"notebook": "uc2-impact-estimation", "area": 1, "stac_catalog": "montandon",
                  "stac_collection": "reference-events", "stac_item_id": "..."}   # item fetched live (or "stac_item": <pk>)
POST /api/runs/  {..., "filters": {"hazard": ["flood"]}}   # manual tags; they win over derived ones
POST /api/runs/<id>/cancel/  ·  POST /api/runs/<id>/retry/
POST /api/runs/upload/  multipart: file=@notebook.zip, title, summary, description, area, stac_item,
                        filters='{"hazard": ["flood"]}', notebook_path   # staff only; rendered, not executed
POST /api/templates/  multipart: file=@template.zip, slug, metadata='{...template.yml keys}'   # template editors; starts inactive
PATCH /api/templates/<slug>/  {"is_active": true, "filters": {...}}
POST /api/templates/<slug>/versions/  multipart: file=@template.zip, note, make_current   # e.g. from CI
POST /api/templates/<slug>/versions/<n>/make-current/
GET  /api/areas/geojson/?country=NPL&level=2   simplified boundaries for the map picker
GET  /api/catalogs/  ·  /api/catalogs/<slug>/collections/  ·  /api/trigger-rules/  ·  /api/batches/
GET  /api/stac-items/?catalog__slug=montandon&collection=reference-events&hazard=flood
```

Session or token auth (`rest_framework.authtoken`). Reads are public and creating runs needs a user,
except catalogs (reads need a login, writes need staff; API keys are write-only) and filters (writes
need staff). Templates, runs and STAC items all accept `?<filter key>=<option>`. On `/api/runs/`,
`status` is an exact match (`published`, `failed`, …); there is no `all` value like on the browse page.

## Layout

```
config/                 settings, celery, urls
factory/catalog/        Facet/FacetOption (filters), Country, AdminArea (PostGIS), GADM/COD importers;
                        facets.py = how runs get tagged and how querysets are filtered
factory/notebooks/      AnalysisNotebook, NotebookRun, RunBatch; parameter schemas; the pipeline
factory/events/         StacCatalog, StacItem, TriggerRule, STAC client + profiles, poller, write-back
factory/notifications/  Notification + delivery
factory/api/            DRF viewsets
factory/web/            HTMX views, templates, static; stac_picker.py = the run form's catalog/collection/item picker
notebook_templates/     example notebook templates (one folder each; imported with sync_templates)
examples/stac_items/    sample STAC items for local testing
tests/                  pytest (needs the PostGIS database)
helm/                   Helm chart
deploy/ovh/             test deployment on devseed's OVH cluster
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
* **Write-back** uses the STAC Transactions extension (`POST/PUT /collections`, `/items`); it is off
  by default per catalog and needs an API key with write rights.
* **Uploaded notebooks run their own HTML/JS** in readers' browsers, and published pages are served from the
  app's host (`/published`). That is why uploads are staff only. Serve published files from a separate
  domain (`PUBLISHED_PUBLIC_BASE_URL`) before opening uploads to other users.
* **Catalog API keys** are stored unencrypted in the database and only support Bearer auth.
