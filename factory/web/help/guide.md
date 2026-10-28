{{SITE_NAME}} turns Jupyter notebooks into published, searchable analyses. A notebook template is run for a place (an admin area) and optionally a STAC item (for example a disaster event). The result is rendered as a static web page that anyone can open.

The guide has three parts:

* **Everyone**: [browsing](#browsing-analyses), [viewing](#viewing-an-analysis), [running a template](#running-a-template) and [STAC items](#stac-items).
* **Staff**: [creating templates](#creating-notebook-templates), [uploading notebooks](#uploading-notebooks), [filters](#managing-filters) and [STAC catalogs](#managing-stac-catalogs).
* **Operators and developers**: [setting up an instance](#setting-up-an-instance) and [the API](#using-the-api).

## Concepts

| Term | Meaning |
|---|---|
| **Template** | A parameterised notebook (`notebook.ipynb` + `template.yml`) that staff publish. Users run it for an area with their own parameter values. |
| **Run** (analysis) | One execution of a template, or one uploaded notebook. It goes through the stages *queued → fetching → executing → rendering → publishing → published*. If something goes wrong it ends as *failed* or *cancelled*. |
| **Batch** | A group of runs created together, one per area. A batch is created when you pick several areas or *every area at a level*. |
| **Area** | An administrative boundary: a country (level 0), province (1), district (2), and so on. Areas come from GADM, OCHA COD or custom files. |
| **STAC catalog** | A STAC API (such as Montandon) that runs can take items from. |
| **STAC item** | One record from a catalog, such as a disaster event, which a run analyses. |
| **Filter** | A way to tag and search runs, such as *Hazard* or *Use case*. Each filter has a fixed list of **options**. |
| **Trigger rule** | Automation that starts a template whenever a matching STAC item appears in a catalog. |

What you can do depends on who you are:

| | Anonymous | Logged in | Staff |
|---|---|---|---|
| Browse and view published analyses, templates and STAC items | ✓ | ✓ | ✓ |
| Read the API (except catalogs) | ✓ | ✓ | ✓ |
| Run templates, cancel and retry runs | | ✓ | ✓ |
| Edit title, description and tags of your own runs | | ✓ | ✓ (any run) |
| Get notifications | | ✓ | ✓ |
| Upload notebooks, manage filters and catalogs, use the Django admin | | | ✓ |

## Browsing analyses

The **Analyses** page ([`/`]({{BASE_URL}})) lists runs as cards, newest first. By default it shows only **published** runs.

The sidebar narrows the list. Results update as you type or click:

* **Search** matches the template title, the run's title, summary and description, the area and country, and the STAC item's title, id and correlation id.
* **Country** limits results to one country.
* **Filters** such as *Hazard* or *Use case* (whichever staff have set to show in browse). Ticking several options of the same filter shows runs with *any* of them. Selections in different filters must *all* match.
* **Source** is a specific template or *Uploaded notebooks*.
* **Date** is the last 30 days, the last 90 days, or all time.
* **Status** is *Published* (the default), *In progress*, *Failed* or *All*.
* **Sort** is newest, oldest, by area or by title.

The URL updates as you filter, so you can bookmark or share a filtered view.

Click a card to open it. Published runs open in the viewer. Runs still in progress, and failed runs, open the run page.

## Viewing an analysis

### The viewer

A published run opens the rendered notebook inside the page. The bar above it has:

* **Open in new tab**: the full published page on its own (also its permanent public link).
* **Download .ipynb**: the executed notebook, with outputs, to open in Jupyter.

Code cells are collapsed by default in the rendered page. Click a cell to expand it. Maps and charts are interactive in the browser, without needing a running kernel.

### The run page

Every run has a details page (`/runs/<id>/`). It shows:

* **Status and stages**: a stepper showing queued → fetching inputs → executing → rendering → publishing. While the notebook executes, a progress bar counts cells. The page refreshes itself until the run finishes.
* **Error**: if the run failed, the error and the tail of the run log.
* **Facts**: the template (or uploaded file) and its version, the area (with its parent areas), the STAC item, the filter tags, the parameters used, who started the run and when, and the batch it belongs to.
* **Artifacts**: files the notebook wrote to its output folder (CSV, GeoJSON, PNG…), each downloadable.
* **Actions**: *Open result*, *Direct link* and *Download .ipynb* once the run is published. *Cancel* while it is running. *Retry* after it fails or is cancelled.

### Editing titles, descriptions and tags

A run you started (or any run, if you are staff) can be edited from its page:

* **Details**: title, summary and a Markdown description. These replace the template's defaults on cards and in search.
* **Tags**: the run's filter options, for example correcting its hazard. Tags set by hand always win over tags derived automatically (see [Managing filters](#managing-filters)).

### Batches

A batch page (`/batches/<id>/`) lists every run in the batch with counts per status. It refreshes while runs are still going. Open any run from the list.

## Running a template

You need to be logged in.

1. Open **Templates** and pick one. The template page explains what it does, lists its parameters and defaults, and shows recent runs.
2. Click **Run this analysis →**. The run form has up to four steps:
    * **Area**: choose a country and admin level, then click areas on the map (or use the list). Select one area for a single run, or several for a batch. **Run for every area at this level** fans out over all areas at the chosen level.
    * **STAC item** (only for templates that need one): pick a catalog, then a collection, then an item from the newest 25, or *Find by item id*. Templates suggest suitable catalogs and collections. Tick **Show all catalogs & collections** to ignore the suggestions.
    * **Parameters**: a form generated from the template, with defaults filled in and a short description under each field. Invalid values are flagged before the run starts.
    * **Tags**: filter options for this run. Tags that the template or a parameter already decides are shown read-only. Optionally add email addresses under **Also notify**.
3. Submit. You land on the run page (or the batch page) and can watch it progress.

When the run finishes you get an in-app notification (the 🔔 in the top bar) and an email.

**Cancel** stops a queued or running run. **Retry** starts a failed or cancelled run again with the same inputs.

## STAC items

**STAC items** (`/items/`) lists every STAC item the factory knows about. This includes items picked in run forms and items found by the catalog poller. You can search, filter by catalog, and filter by any filter that is linked to a STAC property (such as hazard codes).

An item's page shows its properties, the runs that used it, and the templates that take a STAC item. Under **Run an analysis**, click a template to open its run form with this item (and its country, where known) preselected.

## Creating notebook templates

*Template editors* (staff with the **Can change analysis notebook** permission; see [Users, permissions and API tokens](#users-permissions-and-api-tokens)). A template is a notebook with a cell tagged `parameters`, plus settings: what it needs (an area, a STAC item), its parameters (which become the run form) and its filters. Templates are uploaded and edited in the app, and every change that affects how they run is kept as a **version**.

### Uploading a template

1. Under **Templates**, click **New template** ([`/templates/-/new/`]({{BASE_URL}}templates/-/new/)) and pick a `.zip` of the template's folder, or a single `.ipynb`. The page checks the file straight away. It lists the notebooks in the zip, the parameters it detects, and any problems: no `parameters` cell, imports that are not installed on the workers, or reserved parameters that are not declared.
2. Click **Upload and review**. The template is created **inactive**, so only template editors see it.
3. On the edit page, check the details, filters, inputs and **parameters**. Parameters are detected from the `parameters` cell: one per `name = value` line, with its type taken from the value and a trailing `# comment` used as the description. Reserved names are skipped. Refine each parameter (title, choices as `value | label` lines, minimum / maximum / step, widget, required, the filter it tags) or switch to **YAML** to edit the JSON Schema directly. **Check and preview** shows the run form exactly as users will see it.
4. Use **Test run** to try it, then tick **Active** and save to publish it.

A `template.yml` next to the notebook (see below) fills in all the settings, so a folder exported from the app (or kept in git) can be uploaded as it is. Every file next to the notebook is shipped with it and is available when it runs. The notebook may only import packages that are installed in the worker image.

### Versions

A version freezes the notebook and its files together with the parameters and inputs that match them. Each run records the version it executed, which is shown next to the template on the run page.

* **Upload new version** replaces the notebook and files. Without a `template.yml` in the upload, the current settings are kept and new variables in the `parameters` cell are added. Untick *Make it the current version* to stage a version without switching to it.
* Saving the edit page changes the current version **in place while no run has used it**. Once a run has used it, saving makes a new version that reuses the same files.
* **Versions** lists them all. **Make current** rolls back (or forward), and **Download** gives a zip with a `template.yml` generated from the settings.
* Queued and running runs always finish on the version they were started with. **Retry** uses the current version, and says so if the old parameters no longer fit.
* When the parameters change, trigger rules whose fixed parameters would no longer validate are listed as warnings.

Title, summary, description, filters and the active flag are not versioned. A template that has never run can be deleted, together with its versions and trigger rules. Otherwise, untick *Active* to hide it.

### Folder layout

```
<slug>/
├── template.yml     settings + parameter schema (optional when uploading in the app)
├── notebook.ipynb   the notebook; its first code cell is tagged `parameters`
└── ...              helper modules / small data files (shipped with every run)
```

### template.yml

```yaml
title: Population & infrastructure risk exposure
summary: One line for cards.
description: |
  Markdown shown on the template page.
filters:                  # filter key -> option keys (must exist on the Filters page)
  use_case: [uc1]         #   exactly one option: every run is tagged with it
  hazard: [flood, cyclone] #  several: the choices offered in the run form
version: "0.2"
requires_area: true       # run needs an admin area
area_levels: [1, 2]       # optional: restrict admin levels
requires_stac_item: false # run needs a STAC item
stac_hints:               # optional, soft: narrow / preselect the STAC picker
  profile: montandon      #   only offer catalogs with this profile (generic | montandon)
  collections: [reference-events]
  roles: [event]          #   hide items without one of these roles
supports_writeback: false # notebook writes output_dir/stac/ for write-back
estimated_runtime_minutes: 3
parameters:               # JSON Schema; becomes the run form and its validation
  type: object
  properties:
    hazard: {type: string, title: Hazard, enum: [flood, cyclone], default: flood,
             x-enum-labels: {flood: Riverine flood, cyclone: Tropical cyclone}}
    intensity_threshold: {type: number, title: Threshold, minimum: 0, maximum: 1, multipleOf: 0.05, default: 0.5}
    aggregation_level: {type: integer, enum: [1, 2, 3], default: 2}
    infrastructure_layers: {type: array, items: {type: string, enum: [schools, hospitals]}, default: [schools]}
    climate_scenario: {type: string, enum: [current, rcp45], x-widget: radio}
  required: [hazard]
```

**Supported parameter schema features**

* Types: `string`, `number`, `integer`, `boolean`, `array` (arrays need `items.enum`).
* Keywords: `enum`, `default`, `title`, `description`, `minimum`, `maximum`, `multipleOf` (the form's step), `x-enum-labels`.
* `x-widget` picks the form control: `select`, `radio`, `checkboxes`, `slider` or `textarea`.

**Parameters that tag runs.** A parameter named like a filter key (`hazard` above) tags the run with the chosen option, so its `enum` values should be that filter's option keys. Any other parameter can do the same with `x-filter: <filter key>`. An `aggregation_level` parameter chooses which subdivisions are exported for the notebook.

### The parameters cell

The factory injects these **reserved parameters** into every run. Declare them in the `parameters` cell with harmless defaults, so the notebook also runs on its own in Jupyter:

| Name | Content |
|---|---|
| `run_id` | the run's UUID |
| `inputs_dir` | folder with prepared inputs |
| `output_dir` | write artifacts here. They are published next to the page. |
| `area` | `{id, code, name, level, country, country_name, bbox}` or `{}` |
| `area_geojson` | path to a FeatureCollection with the target area |
| `subdivisions_geojson` | path to the subdivisions (children of the area, or the `aggregation_level`) |
| `stac_item`, `stac_item_json` | the run's STAC item as a dict, and as a file on disk |
| `stac_collection`, `stac_api_url`, `stac_catalog_profile` | the item's collection, catalog root URL and profile |
| `filters` | the run's tags, e.g. `{"hazard": ["flood"]}` |
| `hazard` | first hazard option, or `""` (kept for older templates) |

```python
run_id = ""
inputs_dir = ""
output_dir = "outputs"
area = {}
area_geojson = ""
subdivisions_geojson = ""
stac_item = {}
stac_item_json = ""
stac_collection = ""
stac_api_url = ""
stac_catalog_profile = ""
filters = {}
hazard = ""
# ...followed by the template's own parameters with their defaults
```

The catalog's API key is passed to the kernel as the `STAC_API_KEY` environment variable (with `STAC_API_URL`), never as a parameter, so it never appears in the published page.

### Output conventions

* Anything written to `output_dir` is published under the run and listed as an artifact.
* Interactive output: anywidget-based widgets (lonboard maps, [manywidgets](https://github.com/developmentseed/manywidgets) charts, stats, toggles and legends) are exported as static widgets that work without a kernel. Classic ipywidgets (`IntSlider`, `VBox`…) only show a "kernel required" placeholder. matplotlib works with the default inline backend. Do not call `matplotlib.use("Agg")`.
* Code cells are collapsed in the published page. Tag a cell `show-input` to keep it open, or `remove-input` to hide its code entirely.
* Write-back: a template with `supports_writeback` writes `output_dir/stac/collection.json` and `output_dir/stac/items/*.json`. These are pushed to the item's catalog if that catalog has write-back enabled.

### Importing templates from a folder

The example templates in `notebook_templates/` (or `NOTEBOOK_TEMPLATES_DIR`), and templates kept in git, can be imported from the command line:

```sh
python manage.py sync_templates                       # new folders become templates; changed folders, new versions
python manage.py sync_templates --dry-run             # show what would change
python manage.py sync_templates --only-missing        # only import templates that don't exist yet
python manage.py sync_templates --force               # also add versions over ones made in the app
python manage.py sync_templates --overwrite-metadata  # also update title, summary, filters, active
python manage.py sync_templates --convert             # turn templates still served from the folder into uploaded ones
python manage.py run_notebook <slug> --area NPL.1_1 -p threshold=0.4   # test a run synchronously from the CLI
```

A folder only becomes a new version when its files or settings changed. A version made in the app is never replaced unless you pass `--force`. On existing templates, title, summary, description, filters and the active flag are only updated with `--overwrite-metadata`. Unknown filter keys or options in `template.yml` are skipped with a warning. The web container no longer imports templates when it starts, so edits made in the app stick.

**Templates in git.** CI can push a new version on each merge with the API (see [Using the API](#using-the-api)). Alternatively, in the Django admin, set *source kind* to `git` and fill in the repository, ref and notebook path; the worker clones and caches the repository.

## Uploading notebooks

*Staff.* Notebooks that were run elsewhere (on a laptop, in JupyterHub…) can be published as-is under **Upload** ([`/uploads/new/`]({{BASE_URL}}uploads/new/)).

1. Choose a **`.zip`** containing the notebook and the files it links to (images, data), or a bare **`.ipynb`**. The file is inspected as soon as you pick it. If the zip holds several notebooks, pick the one to publish. The title is guessed from the notebook's first heading. Warnings (for example, unexecuted cells) are shown before you submit.
2. Fill in the **title**, **summary** and **description** (Markdown). Optionally choose **one area** and a **STAC item**, and set **tags**.
3. Submit. The upload becomes a run that appears in browse (*Source → Uploaded notebooks*), search and the API like any other.

An uploaded notebook is **rendered, not executed**: it goes through *unpacking → rendering → publishing* and shows the outputs saved in the file. The original archive is kept, so **Retry** re-renders it (useful after a theme change).

Limits: 200 MB per upload, 1 GB uncompressed and 2,000 files. Operators can change these with the `NOTEBOOK_UPLOAD_MAX_*` settings. Archives with paths outside the archive, or with symlinks, are refused.

Uploaded notebooks can include arbitrary HTML and JavaScript, which runs in readers' browsers. That is why only staff can upload.

## Managing filters

*Staff.* Filters (also called facets) are the categories runs are tagged and searched by. Manage them under **Filters** ([`/filters/`]({{BASE_URL}}filters/)). The page lists each filter, its options, whether it allows several options per run, its STAC property and how many runs use it.

### Filter fields

| Field | Meaning |
|---|---|
| **Key** | Used in URLs and the API (`?hazard=flood`). Names the app already uses (`q`, `status`, `country`, `catalog`, `sort`, …) are rejected. |
| **Label**, **Description** | Shown in the UI. |
| **Multiple** | Whether a run can have several options (checkboxes) or only one (a radio button or select). |
| **STAC property** | Optional. An item property, such as `monty:hazard_codes`, whose values are mapped onto options. |
| **Show in browse** | Whether the filter appears in the Analyses sidebar. |
| **Order** | Position in sidebars and forms. |

Each **option** has a key, a label, an optional emoji **icon** and **color** (used for its pill), **match codes** (comma-separated prefixes matched against the STAC property's values, e.g. `FL, MH0600` for flood), and an order.

### How runs get their tags

For each filter, a run takes its options from the **first** of these sources that gives any:

1. **Manual**: chosen in the run form's *Tags*, edited on the run page, or sent as `filters` through the API.
2. **A template parameter** named after the filter key, or one marked `x-filter: <key>`.
3. **The STAC item**: options whose match codes match the filter's STAC property.
4. **The template**: when its `template.yml` lists exactly one option for that filter.

Option keys form a controlled vocabulary. A value that is not an existing option is ignored, and a warning is logged.

A filter can't be deleted while a run, template, item or trigger rule uses it. Remove those uses first, or keep the filter and hide it from browse.

## Managing STAC catalogs

*Staff.* **Catalogs** ([`/catalogs/`]({{BASE_URL}}catalogs/)) lists the STAC APIs runs can take items from.

| Field | Meaning |
|---|---|
| **Name**, **Slug** | The slug is used in the API (`stac_catalog`, `catalog__slug`). |
| **URL** | The STAC API root, e.g. `https://earth-search.aws.element84.com/v1`. |
| **API key** | Optional. Sent as `Authorization: Bearer <key>`. It is never shown again. Leave the field blank when editing to keep the stored key, or tick *remove* to clear it. It is stored unencrypted. |
| **Profile** | `generic` reads core STAC properties only. `montandon` also reads `monty:corr_id` (shown as the correlation id) and `monty:country_codes`, which are used by trigger-rule country filters and to preselect a country. |
| **Write-back enabled** | Lets templates with `supports_writeback` push results to this catalog (needs a key with write rights). Off by default. |
| **Active** | Inactive catalogs are hidden from pickers and the poller. |

Click **Test connection** to check the URL and key. It shows the catalog's title, how many collections it has, and the first few collection ids.

A catalog with stored items can't be deleted. Deactivate it instead.

## Setting up an instance

*Operators.* This section covers what it takes to get a working instance from scratch. For container and Kubernetes deployment details, see `README.md`, `docker-compose.yml` and `helm/README.md` in the repository.

### First-time checklist

1. [Install the prerequisites and configure `.env`](#install-and-configure).
2. [Create the database and run migrations](#database).
3. [Create an admin user](#users-permissions-and-api-tokens).
4. [Import countries and admin boundaries](#importing-countries-and-admin-boundaries). Without them the map picker is empty and area runs cannot start.
5. [Create filters, add STAC catalogs and load templates](#filters-catalogs-and-templates).
6. [Start the workers](#running-the-workers) (or rely on in-process mode on a laptop).
7. Optionally, [set up trigger rules](#automation-with-trigger-rules).

To try everything locally, `seed_demo` covers steps 4 and 5 in one go:

```sh
python manage.py migrate
python manage.py seed_demo --countries NPL
python manage.py createsuperuser
python manage.py runserver
```

### Install and configure

Requirements:

* Python 3.12+ and [uv](https://docs.astral.sh/uv/)
* PostgreSQL with **PostGIS**
* GDAL and GEOS (`brew install gdal geos` on macOS)
* Node 18+ with **mystmd ≥ 1.10** (`npm i -g mystmd`)

```sh
uv sync
npm i -g mystmd
cp .env.example .env    # then edit it
```

With Docker, `docker compose up --build` starts PostGIS, Redis, MinIO, web, worker and beat. Run the commands below through `docker compose run --rm web python manage.py …`.

The settings that matter most (all are in `.env.example`):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgis://user:pass@host:5432/db`. PostGIS is required. |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Standard Django settings. Set a real secret and `DEBUG=false` in production. |
| `SITE_NAME`, `PUBLIC_BASE_URL` | Site title, and the public URL used in notification emails. |
| `CELERY_BROKER_URL` | Empty runs notebooks in-process (`NOTEBOOK_EAGER_WORKERS` at a time). A Redis URL uses real workers. |
| `PUBLISHED_STORAGE_BACKEND` | Where rendered sites go: `local`, `s3` or `azure`. |
| `PUBLISHED_ROOT`, `PUBLISHED_URL` | `local` backend: the folder, and the URL prefix served by Django. |
| `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ADDRESSING_STYLE` | `s3` backend (AWS, MinIO, …). |
| `AZURE_ACCOUNT_NAME`, `AZURE_ACCOUNT_KEY` or `AZURE_CONNECTION_STRING`, `AZURE_CONTAINER` | `azure` backend. Without a key, the app signs in with `DefaultAzureCredential`. |
| `PUBLISHED_PUBLIC_BASE_URL` | Optional CDN or separate domain for published files. Recommended, especially if uploads are used. |
| `MYST_COMMAND`, `MYST_TEMPLATE`, `MYST_HIDE_CODE` | How mystmd is invoked (e.g. `npx -y mystmd@1.10.1`), the site theme, and whether code is collapsed. |
| `NOTEBOOK_TEMPLATES_DIR`, `NOTEBOOK_WORK_DIR` | Example templates for `sync_templates`, and where runs execute. |
| `NOTEBOOK_TEMPLATE_MAX_BYTES`, `NOTEBOOK_TEMPLATE_CACHE_DIR` | Size limit for uploaded templates, and where workers unpack them. Template bundles are kept in the published storage under `templates/`, so don't put secrets in them. |
| `NOTEBOOK_CELL_TIMEOUT`, `NOTEBOOK_STALE_RUN_HOURS` | Per-cell timeout (seconds). Runs without progress for this long are marked failed. |
| `NOTEBOOK_UPLOAD_MAX_BYTES`, `…_MAX_UNCOMPRESSED_BYTES`, `…_MAX_FILES` | Upload limits. |
| `STAC_TIMEOUT`, `STAC_POLL_INTERVAL_SECONDS`, `STAC_POLL_LOOKBACK_DAYS` | STAC HTTP timeout, how often the poller runs, and how far back it looks the first time. |
| `MONTANDON_STAC_URL`, `MONTANDON_API_TOKEN` | Only used by `seed_demo` to create the Montandon catalog. |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Notification email. The default backend prints email to the console. |

STAC catalogs and filters are **not** configured through the environment. Staff manage them in the UI.

### Database

```sh
createdb notebook_factory
psql -d notebook_factory -c "CREATE EXTENSION postgis;"
python manage.py migrate
```

### Users, permissions and API tokens

```sh
python manage.py createsuperuser
```

* **Adding users**: **Admin → Users → Add user**. Any active user can log in and run templates.
* **Staff**: tick **Staff status** on a user to unlock Upload, Filters, Catalogs and the Admin link. To manage models in the Django admin, a staff user also needs *Superuser status* or the relevant model permissions.
* **Template editors**: give a user the *notebooks | analysis notebook | Can change analysis notebook* permission (and *Can add* to create new templates), or put them in a group that has it. Superusers have it already. It is separate from Staff because an uploaded template runs its code on the workers, which hold the database, storage and catalog credentials.
* **API tokens**: **Admin → Auth Token → Tokens → Add**, or:

```sh
python manage.py drf_create_token <username>
```

### Importing countries and admin boundaries

Runs are scoped to **admin areas**: countries (level 0) and their subdivisions (levels 1, 2, …). The areas drive the map picker, *run for every area at this level*, and the `area`, `area_geojson` and `subdivisions_geojson` parameters notebooks receive. **Nothing works for area-based templates until boundaries are imported.** Countries are created automatically from the boundary data.

**From GADM** (easiest; downloads GADM 4.1 GeoJSON and caches it in `data/gadm/`):

```sh
python manage.py import_gadm NPL VNM               # levels 0,1,2 by default
python manage.py import_gadm UKR --levels 0,1,2,3
```

**From OCHA COD or your own files** (any GDAL-readable file: GeoJSON, GeoPackage, Shapefile):

```sh
# COD: reads ADM{n}_PCODE / ADM{n}_EN, parent ADM{n-1}_PCODE, country ADM0_PCODE
python manage.py import_admin_areas npl_adm0.shp --level 0 --source cod
python manage.py import_admin_areas npl_adm1.shp --level 1 --source cod

# custom attribute names
python manage.py import_admin_areas districts.gpkg --level 2 --source custom \
    --country NPL --code-field DIST_CODE --name-field DIST_NAME --parent-field PROV_CODE --layer 0
```

* **Import levels in order (0, then 1, then 2…).** Each area is linked to its parent by the parent code, and the parent must already exist.
* Parents are only looked up within the **same source**. Import a whole hierarchy from one source, and don't mix GADM provinces with COD districts.
* `--country` forces the ISO3 code when the file doesn't carry one. `--layer` picks a layer in multi-layer files.
* Re-importing is safe. Areas are updated in place, matched by source and code.

`seed_demo --countries NPL,VNM --levels 0,1,2` calls `import_gadm` for you (`--countries ""` or `--skip-boundaries` skips it).

To check the result, open [`/api/countries/`]({{BASE_URL}}api/countries/) and `/api/areas/?country=NPL&level=1`, or start a run and look at the map.

### Filters, catalogs and templates

* **Filters**: create them on **Filters** before syncing templates, because `template.yml` refers to their option keys. `seed_demo` creates *Hazard* and *Use case* with sensible options.
* **Catalogs**: add each STAC API on **Catalogs** and click *Test connection*. `seed_demo` adds Montandon (staging), using `MONTANDON_API_TOKEN` as its key if set. Otherwise, add the key on the catalog page.
* **Templates**: upload them under **Templates → New template**, or import the examples in `NOTEBOOK_TEMPLATES_DIR` once with `python manage.py sync_templates` (`seed_demo` does this too). See [Creating notebook templates](#creating-notebook-templates).

`seed_demo` is safe to re-run. It also creates two **disabled** example trigger rules.

### Running the workers

**In-process mode** (no `CELERY_BROKER_URL`): runs execute in a thread pool inside the web process, and published files are served by Django at `/published/`. This is fine on a laptop. A server restart interrupts running notebooks.

**With a queue** (`CELERY_BROKER_URL=redis://…`), run three processes:

```sh
python manage.py runserver                      # or gunicorn config.wsgi
celery -A config worker -l info --concurrency 2 # executes and renders notebooks
celery -A config beat -l info                   # scheduler
```

Beat runs the **STAC poller** every `STAC_POLL_INTERVAL_SECONDS` (default 600) and cleans up old work folders hourly. Without beat, schedule `python manage.py poll_catalogs` with cron (`--dry-run` only counts new items).

### Automation with trigger rules

Trigger rules start a template automatically when the poller finds matching STAC items. Manage them in **Admin → Trigger rules**:

* **Catalog** and **collections** to watch, plus optional **roles**, **filter options**, **country codes** (needs the `montandon` profile) and a CQL2-JSON **search filter**.
* **Notebook**: the template to run.
* **Parameter template**: parameters for the run. String values may use Jinja, e.g. `{{ item.correlation_id }}`, `{{ filters.hazard[0] }}` or `{{ props['monty:hazard_codes'][0] }}`.
* **Area level**: fan out over every area at that level in the item's countries. Leave it empty for one country-level run.
* **Rerun on update**: run again when the item changes. Otherwise each item triggers a rule once.
* **Notify emails**: extra recipients.

Test a rule without waiting for the poller:

```sh
python manage.py ingest_item --catalog montandon --file examples/stac_items/nepal-earthquake.json
python manage.py ingest_item --catalog montandon --collection reference-events --item <id>
python manage.py ingest_item --catalog montandon --file item.json --no-fire   # store only, don't trigger
```

### Maintenance and troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Map picker is empty / "No areas loaded" | Boundaries not imported for that country or level. See [Importing countries and admin boundaries](#importing-countries-and-admin-boundaries). |
| Runs stay *queued* | A broker is configured, but no Celery worker is running. |
| Runs stuck mid-way after a restart | `python manage.py stale_runs` lists them. Add `--requeue` to re-run them or `--fail` to mark them failed (`--older-than 0.5` hours by default). |
| Rendering fails with "myst not found" | Install mystmd ≥ 1.10, or set `MYST_COMMAND="npx -y mystmd@1.10.1"`. |
| Catalog test or picker fails with 401/403 | Missing or wrong API key on the catalog page. |
| Write-back skipped | The catalog doesn't have *Write-back enabled*, or its key lacks write rights. The run log says which. |
| Upload rejected as too large behind a proxy | Raise the proxy's body size limit (e.g. ingress-nginx `proxy-body-size`) as well as `NOTEBOOK_UPLOAD_MAX_BYTES`. |
| Widgets show "kernel required" | The notebook uses classic ipywidgets. Use anywidget-based ones (lonboard, manywidgets). |
| Errors importing GeoDjango / GDAL | GDAL and GEOS are not installed, or not found. Set `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH`. |

To debug a template outside the web app, `python manage.py run_notebook <slug> --area <code> -p key=value` runs it synchronously and prints the log. The work folder is kept under `NOTEBOOK_WORK_DIR` when `NOTEBOOK_KEEP_WORKDIRS=true`.

### Command reference

| Command | Purpose |
|---|---|
| `seed_demo [--countries NPL,VNM] [--levels 0,1,2] [--skip-boundaries]` | Demo setup: filters, boundaries, templates, the Montandon catalog and example rules. |
| `import_gadm ISO3 [ISO3 …] [--levels 0,1,2] [--cache-dir DIR]` | Download and import GADM 4.1 boundaries. |
| `import_admin_areas PATH --level N [--source gadm|cod|custom] [--country ISO3] [--code-field F --name-field F --parent-field F] [--layer N]` | Import boundaries from a file. |
| `sync_templates [--dir DIR] [--dry-run] [--only-missing] [--force] [--overwrite-metadata] [--convert] [--deactivate-missing]` | Import template folders (new templates, or new versions when changed). |
| `run_notebook SLUG [--area CODE|ID] [--stac-item PK|ID] [--catalog SLUG --collection ID --item ID] [-p key=value …] [--async]` | Run a template from the CLI (inline, or queued with `--async`). |
| `ingest_item --catalog SLUG (--file PATH | --collection ID --item ID) [--no-fire]` | Store a STAC item and fire matching trigger rules. |
| `poll_catalogs [--dry-run]` | Poll catalogs with enabled rules once. |
| `stale_runs [--older-than HOURS] [--fail | --requeue]` | Find, fail or requeue runs that stopped making progress. |
| `drf_create_token USERNAME` | Create an API token. |

## Using the API

Everything in the UI is also available as a REST API under [`/api/`]({{BASE_URL}}api/). Interactive documentation is at [`/api/docs/`]({{BASE_URL}}api/docs/) and the OpenAPI schema is at [`/api/schema/`]({{BASE_URL}}api/schema/).

### Authentication

* **Reading** templates, runs, batches, areas, STAC items and filters needs no login. Catalogs need a login.
* **Creating** runs, and cancelling or retrying them, needs a user. Uploads and writes to filters and catalogs need **staff**.
* Send a token (see [Users, permissions and API tokens](#users-permissions-and-api-tokens)) in the `Authorization` header:

```sh
export NF={{BASE_URL}}api
export TOKEN=<your token>
curl -H "Authorization: Token $TOKEN" $NF/runs/
```

In the browser, the API also accepts your logged-in session.

### Lists, search and filters

Paginated endpoints return `{"count", "next", "previous", "results"}` with 50 results per page (`?page=2`). Most lists accept `?search=` and `?ordering=` (e.g. `-created_at`).

Templates, runs and STAC items accept any filter key: `?hazard=flood&hazard=cyclone&use_case=uc2`. Repeating a key means *any of*, and different keys must *all* match. On `/api/runs/`, `status` is an exact match (`published`, `failed`, …). Unlike browse, all runs are returned unless you filter.

| Endpoint | Methods | Notes |
|---|---|---|
| `/api/templates/` | GET | Lookup by slug. Includes the parameter schema and defaults. Filter: `requires_stac_item`, `requires_area`. |
| `/api/runs/` | GET, POST | Filter: `notebook`, `country`, `area`, `stac_item`, `catalog`, `status`, `trigger`, `batch`, `since`, `until`. |
| `/api/runs/<id>/` | GET | Adds `log` and `notify_emails`. |
| `/api/runs/<id>/cancel/`, `/retry/` | POST | Logged in. |
| `/api/runs/upload/` | POST (multipart) | Staff. Publishes a notebook without executing it. |
| `/api/batches/` | GET | Includes `status_counts`. |
| `/api/countries/` | GET | Countries with imported areas. |
| `/api/areas/` | GET | Filter: `country`, `level`, `parent`, `source`. Search name or code. |
| `/api/areas/geojson/` | GET | FeatureCollection. Needs `country`, `level` or `parent`. Optional `simplify`. |
| `/api/areas/<id>/geojson/` | GET | One area as a Feature. |
| `/api/stac-items/` | GET | Filter: `catalog__slug`, `collection`, `correlation_id`. |
| `/api/catalogs/` | GET, POST, PUT, PATCH, DELETE | Read: logged in. Write: staff. `api_key` is write-only (`has_api_key` is returned). |
| `/api/catalogs/<slug>/collections/` | GET | The catalog's collections, fetched live (502 if the catalog is unreachable). |
| `/api/filters/`, `/api/filter-options/` | GET, POST, PUT, PATCH, DELETE | Write: staff. Filters are looked up by key. Options can be filtered by `facet__key`. |
| `/api/trigger-rules/` | GET | Read-only. |

Run status can also be polled cheaply at `/runs/<id>/status.json` (outside `/api/`). It returns `{id, status, progress, output_url, error}`.

### Examples

**Find published flood analyses in Nepal:**

```sh
curl "$NF/runs/?status=published&country=NPL&hazard=flood"
```

**Look up a template and an area:**

```sh
curl $NF/templates/uc1-risk-exposure/                    # parameter_schema + parameter_defaults
curl "$NF/areas/?country=NPL&level=1&search=Koshi"       # → results[].id
```

**Start a run for one area** (returns `202` with the run):

```sh
curl -X POST $NF/runs/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{
  "notebook": "uc1-risk-exposure",
  "area": 12,
  "parameters": {"hazard": "flood", "intensity_threshold": 0.4},
  "filters": {"use_case": ["uc1"]},
  "notify_emails": ["team@example.org"]
}'
```

**Start a batch**, either for several areas (`"areas": [12, 13, 14]`) or for every area at a level of a country. The response is the batch:

```sh
curl -X POST $NF/runs/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{
  "notebook": "uc1-risk-exposure", "country": "NPL", "area_level": 2,
  "parameters": {"hazard": "flood"}
}'
```

**Use a STAC item.** Either pass a stored item's id (`"stac_item": 42`), or name it in a catalog and the factory fetches it:

```sh
curl -X POST $NF/runs/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{
  "notebook": "uc2-impact-estimation", "area": 1,
  "stac_catalog": "montandon", "stac_collection": "reference-events", "stac_item_id": "<item id>"
}'
```

**Follow a run, then cancel or retry it:**

```sh
curl {{BASE_URL}}runs/<run id>/status.json
curl $NF/runs/<run id>/                                   # full record incl. log, output_url, artifacts
curl -X POST -H "Authorization: Token $TOKEN" $NF/runs/<run id>/cancel/
curl -X POST -H "Authorization: Token $TOKEN" $NF/runs/<run id>/retry/
```

**Upload a notebook** (staff, multipart; `notebook_path` is needed when the zip holds several notebooks):

```sh
curl -X POST $NF/runs/upload/ -H "Authorization: Token $TOKEN" \
  -F file=@analysis.zip -F notebook_path=analysis/report.ipynb \
  -F title="Flood exposure, Koshi" -F summary="One-off analysis" \
  -F area=12 -F 'filters={"hazard": ["flood"]}'
```

**Templates** (template editors; see [Creating notebook templates](#creating-notebook-templates)). A new template starts inactive; the response lists `warnings`:

```sh
curl -X POST $NF/templates/ -H "Authorization: Token $TOKEN" \
  -F file=@my-template.zip -F slug=flood-exposure -F 'metadata={"version": "1.0"}'
curl -X PATCH $NF/templates/flood-exposure/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"is_active": true, "filters": {"hazard": ["flood"]}}'
curl -X POST $NF/templates/flood-exposure/versions/ -H "Authorization: Token $TOKEN" \
  -F file=@my-template.zip -F note="CI build $GIT_SHA"          # add make_current=false to stage it
curl $NF/templates/flood-exposure/versions/ -H "Authorization: Token $TOKEN"
curl -X POST $NF/templates/flood-exposure/versions/2/make-current/ -H "Authorization: Token $TOKEN"
curl "$NF/templates/?include_inactive=1" -H "Authorization: Token $TOKEN"
```

`metadata` takes `template.yml` keys and overrides the `template.yml` in the zip.

**Manage filters** (staff):

```sh
curl -X POST $NF/filters/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"key": "sector", "label": "Sector", "multiple": true, "show_in_browse": true}'
curl -X POST $NF/filter-options/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"facet": "sector", "key": "health", "label": "Health", "icon": "🏥"}'
```

**Boundaries as GeoJSON:**

```sh
curl "$NF/areas/geojson/?country=NPL&level=1&simplify=0.01"
```

### Responses and errors

A run looks like this (abridged):

```json
{
  "id": "6c1e…", "notebook": "uc1-risk-exposure", "is_upload": false,
  "title": "…", "status": "published", "progress": {"cell": 23, "total": 23},
  "parameters": {"hazard": "flood"},
  "area": {"id": 12, "code": "NPL.1_1", "name": "Koshi", "level": 1, "country": "NPL"},
  "filters": {"hazard": [{"key": "flood", "label": "Flood", "icon": "🌊", "color": "#2E6FB0"}]},
  "stac_item": null, "batch": null,
  "output_url": "…/runs/6c1e…/index.html", "notebook_url": "…/notebook.ipynb",
  "artifacts": […], "created_at": "…", "finished_at": "…", "detail_url": "…"
}
```

Errors return `400`. Parameter problems come back per field, e.g. `{"parameters": {"intensity_threshold": "must be ≤ 1"}}`. Other problems come back as `{"detail": "…"}` or as field errors such as `{"stac_item_id": ["…"]}`.
