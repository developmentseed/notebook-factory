# Notebook templates

Each template is a folder:

```
notebook_templates/<slug>/
├── template.yml     metadata + parameter schema (loaded by `manage.py sync_templates`)
├── notebook.ipynb   the template; first code cell tagged `parameters`
└── ...              helper modules / small data files (the whole folder is copied for each run)
```

Templates can also live in a git repository: set `source_kind=git`, `source_repo`, `source_ref`
and `notebook_path` on the `AnalysisNotebook` in the admin; the worker clones (shallow) and caches it.

## template.yml

```yaml
title: Population & infrastructure risk exposure
summary: One line for cards.
description: |
  Markdown shown on the template page.
use_case: uc1            # uc1 | uc2 | uc3 | other
hazards: [flood, cyclone] # Hazard keys (see seed_demo)
version: "0.2"
requires_area: true       # run needs an admin area
area_levels: [1, 2]       # optional: restrict admin levels
requires_event: false     # run needs a Montandon event (UC2 / UC3)
supports_writeback: false # notebook writes output_dir/stac/ for Montandon write-back
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
| `event` | dict | the Montandon STAC item, or `{}` |
| `event_json` | str | the same item on disk |
| `montandon_stac_url` | str | STAC API base URL |
| `hazard` | str | hazard key or `""` |

The Montandon token is available as the environment variable `MONTANDON_API_TOKEN` (never as a
parameter, so it does not end up in the published notebook).

## Output conventions

* Anything in `output_dir` is uploaded to `runs/<id>/outputs/` and listed as run artefacts.
* Write-back (UC3): `output_dir/stac/collection.json` + `output_dir/stac/items/*.json`. Items should
  carry `monty:corr_id` and a `derived_from` link; the app adds an `about` link to the published page.
* Widgets: anywidget-based outputs (lonboard maps, [manywidgets](https://github.com/developmentseed/manywidgets)
  `Chart`/`Stat`/`Slider`/`Toggle`/`Legend`/`Row`/`Column`, `LayerToggle`, `jslink`ed controls) are exported as
  static, kernel-free widgets by the vendored myst-anywidget-static-export plugin. Classic ipywidgets
  (`IntSlider`, `VBox`…) only show a "kernel required" placeholder — prefer manywidgets. Keep the data
  behind a widget reasonably small (it is embedded in the page).
* Prefer `manywidgets.Chart` for charts. matplotlib figures also work with the default inline backend
  (never call `matplotlib.use("Agg")`; `fig.savefig(...)` into `output_dir` still gives a PNG artefact).
* Code cells are collapsed in the published page (tag a cell `show-input` to keep it open,
  `remove-input` to drop the code entirely).
