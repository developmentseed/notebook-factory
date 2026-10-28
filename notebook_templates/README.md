# Notebook templates

Templates are uploaded and edited in the app (**Templates → New template**; see the user guide,
"Creating notebook templates"). Every change that affects how a template runs is kept as a version.
The folders here are **examples**. Import them once with `manage.py sync_templates`, or zip one and
upload it.

A template is a folder (zip it to upload it):

```
<slug>/
├── template.yml     settings + parameter schema (optional when uploading: they can be set in the app)
├── notebook.ipynb   the template; first code cell tagged `parameters`
└── ...              helper modules / small data files (shipped with every run)
```

`sync_templates` turns a new folder into a template, and a changed folder into a new version. It
never replaces a version made in the app unless you pass `--force`. Add `--dry-run` to see what it
would do. Templates can also live in a git repository: push versions from CI with
`POST /api/templates/<slug>/versions/`, or set `source_kind=git` in the admin.

## template.yml

```yaml
title: Population & infrastructure risk exposure
summary: One line for cards.
description: |
  Markdown shown on the template page.
filters:                  # filter key -> option keys (see the Filters page); runs inherit a filter
  use_case: [uc1]         #   when exactly one option is listed (several = the choices offered)
  hazard: [flood, cyclone]
version: "0.2"
requires_area: true       # run needs an admin area
area_levels: [1, 2]       # optional: restrict admin levels
requires_stac_item: false # run needs a STAC item from a configured catalog (UC2 / UC3)
stac_hints:               # optional, soft: pre-filter the run form's catalog / collection / item pickers
  profile: montandon      #   only offer catalogs with this profile (generic | montandon)
  collections: [reference-events]  # preselect / narrow the collection list
  roles: [event]          #   hide items without one of these roles
supports_writeback: false # notebook writes output_dir/stac/ for write-back to the item's catalog
estimated_runtime_minutes: 3
parameters:               # JSON Schema (object); becomes the form + validation
  type: object
  properties:
    hazard: {type: string, title: Hazard, enum: [flood, cyclone], default: flood,
             x-enum-labels: {flood: Riverine flood, cyclone: Tropical cyclone}}
    intensity_threshold: {type: number, title: Threshold, minimum: 0, maximum: 1, multipleOf: 0.05, default: 0.5}
    aggregation_level: {type: integer, enum: [1, 2, 3], default: 2}   # special: picks the subdivisions exported
    infrastructure_layers: {type: array, items: {type: string, enum: [schools, hospitals]}, default: [schools]}
    climate_scenario: {type: string, enum: [current, rcp45], x-widget: radio}
  required: [hazard]
```

Filters and their options are managed by staff on the **Filters** page; `filters:` keys and option
keys must already exist there (unknown ones are skipped with a warning).
The older `use_case:` and `hazards:` keys are still read as `filters.use_case` / `filters.hazard`.

A parameter whose name is a filter key (like `hazard` above) tags the run with the chosen option, so
its `enum` values should be that filter's option keys. Any other parameter can do the same with
`x-filter`:

```yaml
    focus_sectors: {type: array, items: {type: string, enum: [health, shelter]}, x-filter: sector}
```

Filters fed by a parameter, or pinned by `filters:` to a single option, are not asked again in the run
form's *Tags*; the rest can be set there (see the main README, "Filters").

`requires_event` is still read as an alias of `requires_stac_item`. Hints never block a run: the user
can tick "show all" in the picker, and the API / CLI ignore them.

Supported: `string | number | integer | boolean | array` (arrays need `items.enum`), `enum`, `default`,
`title`, `description`, `minimum`, `maximum`, `multipleOf` (UI step), `x-widget`
(`select | radio | checkboxes | slider | textarea`), `x-enum-labels`.

## Reserved parameters

The app injects these into every run. Declare them in the `parameters` cell with harmless defaults so
the notebook also runs standalone in Jupyter:

| name | type | content |
|---|---|---|
| `run_id` | str | the run UUID |
| `inputs_dir` | str | folder with prepared inputs |
| `output_dir` | str | write artefacts here (CSV, GeoJSON, PNG, `stac/…`); they are published next to the HTML |
| `area` | dict | `{id, code, name, level, country, country_name, bbox}` or `{}` |
| `area_geojson` | str | path to a FeatureCollection with the target area |
| `subdivisions_geojson` | str | path to the aggregation subdivisions (children of the area, or the level given by an `aggregation_level` parameter) |
| `stac_item` | dict | the run's STAC item, or `{}` |
| `stac_item_json` | str | the same item on disk |
| `stac_collection` | str | the item's collection id |
| `stac_api_url` | str | root URL of the item's catalog |
| `stac_catalog_profile` | str | `generic` or `montandon` (`""` without an item) |
| `filters` | dict | the run's filter tags at creation time, e.g. `{"hazard": ["flood"], "use_case": ["uc1"]}` |
| `hazard` | str | first `hazard` option key or `""` (kept for older templates) |

The kernel also gets the environment variables `STAC_API_URL` and `STAC_API_KEY` for the item's
catalog (empty without an item). The key is never a parameter, so it does not end up in the published
notebook.

A parameters cell that declares all of them:

```python
# --- Reserved parameters (injected by the Notebook Factory; defaults keep the notebook runnable standalone) ---
run_id = ""
inputs_dir = ""
output_dir = "outputs"
area = {}                      # {id, code, name, level, country, country_name, bbox}
area_geojson = ""              # FeatureCollection with the target area
subdivisions_geojson = ""      # FeatureCollection with the aggregation subdivisions
stac_item = {}                 # the run's STAC item (dict)
stac_item_json = ""
stac_collection = ""
stac_api_url = ""              # root of the item's catalog
stac_catalog_profile = ""      # "generic" | "montandon"
filters = {}                   # {"hazard": ["flood"], "use_case": ["uc1"], ...}
hazard = ""
```

## Using the STAC item

`stac_item` is the item as the catalog returned it, so a notebook can read its geometry, properties and
assets directly. To query the same catalog (related items, history, …) use `stac_api_url` and the key
from the environment, sent as a Bearer token:

```python
import os
from pystac_client import Client

key = os.environ.get("STAC_API_KEY")
catalog = Client.open(stac_api_url, headers={"Authorization": f"Bearer {key}"} if key else None)
related = catalog.search(collections=[stac_collection], bbox=stac_item.get("bbox"), max_items=20)
```

Check `stac_catalog_profile` before relying on extension fields: `monty:*` properties are only expected
from `montandon` catalogs. Templates that only make sense for one kind of catalog can say so with
`stac_hints` (above).

## Output conventions

* Anything in `output_dir` is uploaded to `runs/<id>/outputs/` and listed as run artefacts.
* Write-back (UC3): `output_dir/stac/collection.json` + `output_dir/stac/items/*.json`, pushed to the
  catalog the run's STAC item came from, only when that catalog has write-back enabled. Items should carry a
  `derived_from` link (and `monty:corr_id` for Montandon); the app adds an `about` link to the published page.
* Widgets: anywidget-based outputs (lonboard maps, [manywidgets](https://github.com/developmentseed/manywidgets)
  `Chart`/`Stat`/`Slider`/`Toggle`/`Legend`/`Row`/`Column`, `LayerToggle`, `jslink`ed controls) are exported as
  static, kernel-free widgets by the vendored myst-anywidget-static-export plugin. Classic ipywidgets
  (`IntSlider`, `VBox`…) only show a "kernel required" placeholder — prefer manywidgets. Keep the data
  behind a widget reasonably small (it is embedded in the page).
* Prefer `manywidgets.Chart` for charts. matplotlib figures also work with the default inline backend
  (never call `matplotlib.use("Agg")`; `fig.savefig(...)` into `output_dir` still gives a PNG artefact).
* Code cells are collapsed in the published page (tag a cell `show-input` to keep it open,
  `remove-input` to drop the code entirely).
