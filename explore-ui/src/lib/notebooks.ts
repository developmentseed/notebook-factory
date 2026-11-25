import { useEffect, useState } from "react"

type FilterOption = { key: string; label: string }

export type Run = {
  id: string
  title: string
  status: string
  created_at: string
  output_url: string
  notebook_title: string
  summary: string
  disaster_refs: string[]
  template_version: number | null
  parameters: Record<string, unknown>
  area: {
    id: number
    name: string
    level: number
    country: string
    country_name: string
    bbox: [number, number, number, number]
  } | null
  filters: { hazard?: FilterOption[]; use_case?: FilterOption[] }
  stac_item: { id: number; title: string; occurred_at: string | null } | null
}

type RunsPage = { next: string | null; results: Run[] }

/** Fetch every published run, following pagination. */
async function fetchPublishedRuns(signal?: AbortSignal): Promise<Run[]> {
  const runs: Run[] = []
  let url: string | null = "/api/runs/?status=published"
  while (url) {
    const res: Response = await fetch(url, { signal })
    if (!res.ok) throw new Error(`Notebook API returned ${res.status}`)
    const page: RunsPage = await res.json()
    runs.push(...page.results)
    // `next` is an absolute URL on the API host; keep it same-origin.
    url = page.next
      ? new URL(page.next).pathname + new URL(page.next).search
      : null
  }
  return runs
}

/** Demo / smoke-test runs are tagged "other" and are not real analyses. */
function isUserFacing(run: Run): boolean {
  return !run.filters.use_case?.some((u) => u.key === "other")
}

export function usePublishedNotebooks() {
  const [state, setState] = useState<{
    runs: Run[] | null
    error: string | null
  }>({ runs: null, error: null })

  useEffect(() => {
    const controller = new AbortController()
    fetchPublishedRuns(controller.signal)
      .then((runs) =>
        setState({ runs: runs.filter(isUserFacing), error: null })
      )
      .catch((e: unknown) => {
        if (controller.signal.aborted) return
        setState({
          runs: null,
          error: e instanceof Error ? e.message : "Could not load notebooks",
        })
      })
    return () => controller.abort()
  }, [])

  return state
}

/* ------------------------------------------------------------ filters --- */

export type FilterKey = "country" | "hazard" | "useCase"

export const FILTER_KEYS: FilterKey[] = ["country", "hazard", "useCase"]

export const FILTER_LABELS: Record<FilterKey, string> = {
  country: "Country",
  hazard: "Hazard",
  useCase: "Use case",
}

/** Empty list = no restriction. Within a filter any value matches; across
 *  filters every one must. */
export type Filters = Record<FilterKey, string[]> & {
  /** Free text: event title, area, or a disaster ID (GLIDE, EMSR, go:…) */
  q: string
}

export const DEFAULT_FILTERS: Filters = {
  country: [],
  hazard: [],
  useCase: [],
  q: "",
}

export type Option = { value: string; label: string }

export type FilterOptions = Record<FilterKey, Option[]>

/** The values a run has for a filter. */
function runValues(run: Run, key: FilterKey): string[] {
  switch (key) {
    case "country":
      return run.area ? [run.area.country] : []
    case "hazard":
      return (run.filters.hazard ?? []).map((h) => h.key)
    case "useCase":
      return (run.filters.use_case ?? []).map((u) => u.key)
  }
}

/** Options come from the published runs, so every choice has results. */
export function filterOptions(runs: Run[] | null): FilterOptions {
  const labels: Record<FilterKey, Map<string, string>> = {
    country: new Map(),
    hazard: new Map(),
    useCase: new Map(),
  }
  for (const run of runs ?? []) {
    if (run.area) labels.country.set(run.area.country, run.area.country_name)
    for (const h of run.filters.hazard ?? []) labels.hazard.set(h.key, h.label)
    for (const u of run.filters.use_case ?? [])
      labels.useCase.set(u.key, u.label)
  }
  const toOptions = (map: Map<string, string>): Option[] =>
    [...map]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label))
  return {
    country: toOptions(labels.country),
    hazard: toOptions(labels.hazard),
    useCase: toOptions(labels.useCase),
  }
}

export function hasActiveFilters(filters: Filters): boolean {
  return (
    FILTER_KEYS.some((key) => filters[key].length > 0) ||
    filters.q.trim() !== ""
  )
}

/** Does the run pass every active filter, optionally ignoring one? */
function matches(run: Run, filters: Filters, ignore?: FilterKey): boolean {
  for (const key of FILTER_KEYS) {
    if (key === ignore || filters[key].length === 0) continue
    if (!runValues(run, key).some((v) => filters[key].includes(v))) return false
  }
  const q = filters.q.trim().toLowerCase()
  if (q) {
    const haystack = [
      run.title,
      run.area?.name,
      run.area?.country_name,
      run.stac_item?.title,
      ...run.disaster_refs,
    ]
    if (!haystack.some((text) => text?.toLowerCase().includes(q))) return false
  }
  return true
}

export function filterRuns(runs: Run[], filters: Filters): Run[] {
  return runs.filter((run) => matches(run, filters))
}

/**
 * How many analyses each option would show. Counts apply every *other* filter,
 * so they tell you what picking an option adds, and a choice that would give
 * nothing reads 0 before you click it.
 */
export function facetCounts(
  runs: Run[] | null,
  filters: Filters
): Record<FilterKey, Record<string, number>> {
  const counts: Record<FilterKey, Record<string, number>> = {
    country: {},
    hazard: {},
    useCase: {},
  }
  for (const run of runs ?? []) {
    for (const key of FILTER_KEYS) {
      if (!matches(run, filters, key)) continue
      for (const value of runValues(run, key)) {
        counts[key][value] = (counts[key][value] ?? 0) + 1
      }
    }
  }
  return counts
}

/* ---------------------------------------------------------------- map --- */

export type HazardCount = { key: string; label: string; count: number }

export type NotebookPlace = {
  id: number
  name: string
  country: string
  /** [lng, lat] centre of the area's bounding box */
  coordinates: [number, number]
  count: number
  /** How the place's notebooks split across hazards, largest first */
  hazards: HazardCount[]
}

/** One place per area, with the number of notebooks published for it. */
export function groupByPlace(runs: Run[]): NotebookPlace[] {
  const places = new Map<number, NotebookPlace>()
  for (const run of runs) {
    const { area } = run
    if (!area) continue
    const hazard = run.filters.hazard?.[0]
    const key = hazard?.key ?? "unknown"
    const label = hazard?.label ?? "Other hazard"

    let place = places.get(area.id)
    if (!place) {
      const [west, south, east, north] = area.bbox
      place = {
        id: area.id,
        name: area.name,
        country: area.country_name,
        coordinates: [(west + east) / 2, (south + north) / 2],
        count: 0,
        hazards: [],
      }
      places.set(area.id, place)
    }
    place.count += 1
    const entry = place.hazards.find((u) => u.key === key)
    if (entry) entry.count += 1
    else place.hazards.push({ key, label, count: 1 })
  }
  for (const place of places.values()) {
    place.hazards.sort(
      (a, b) => b.count - a.count || a.key.localeCompare(b.key)
    )
  }
  return [...places.values()]
}

/* ------------------------------------------------------------- events --- */

export type Analysis = {
  id: string
  /** The area, or the run date when every analysis in the group shares one area */
  title: string
  /** Level, differing parameters and summary, as one plain line */
  description: string
  /** Run date, shown separately only when the title is an area */
  date: string | null
  url: string
}

export type NotebookGroup = {
  id: string
  title: string
  /** The notebook (template) title from the API, e.g. "Post-disaster impact estimation" */
  notebook: string
  /** Event date, or the latest run for baseline groups */
  date: string
  /** A real disaster event, or a baseline analysis run for many areas */
  kind: "event" | "baseline"
  hazard: string | null
  /** The API's use case key, e.g. "uc2" */
  useCase: string | null
  analyses: Analysis[]
}

const LEVEL_LABELS = ["Country", "Admin 1", "Admin 2", "Admin 3"]

const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  })

const formatTime = (iso: string) =>
  `${new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  })} UTC`

const humanise = (key: string) => {
  const text = key.replace(/_/g, " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const stringify = (value: unknown) =>
  Array.isArray(value) ? value.map(String).join(", ") : String(value)

/** Readable label for one parameter value, from the API's own names. */
function formatParameter(key: string, value: unknown): string {
  switch (key) {
    case "return_period":
      return `${value}-year`
    case "age_group":
      return `Age group: ${humanise(String(value))}`
    case "ghsl_epoch":
      return `GHSL ${value}`
    case "aggregation_level":
      return `Aggregation level ${value}`
    default:
      return `${humanise(key)}: ${stringify(value)}`
  }
}

/** Parameters that are the same for the whole group are noise; these are not. */
function differingTags(runs: Run[]): string[][] {
  const tags = runs.map(() => [] as string[])
  if (runs.length < 2) return tags

  const keys = new Set<string>()
  for (const run of runs) {
    for (const key of Object.keys(run.parameters)) keys.add(key)
  }
  for (const key of keys) {
    if (key === "hazard") continue // already the group's identity
    const values = runs.map((run) => run.parameters[key])
    if (new Set(values.map((v) => JSON.stringify(v ?? null))).size < 2) continue
    values.forEach((value, i) => {
      if (value !== undefined) tags[i].push(formatParameter(key, value))
    })
  }

  const notebooks = runs.map((run) => run.notebook_title)
  if (new Set(notebooks).size > 1) {
    // Prepend so the notebook leads the line when a group mixes them.
    notebooks.forEach((title, i) => tags[i].unshift(title))
  }

  const versions = runs.map((run) => run.template_version)
  if (new Set(versions).size > 1) {
    versions.forEach((v, i) => {
      if (v !== null) tags[i].push(`v${v}`)
    })
  }
  return tags
}

/**
 * Group analyses so the list shows events, not one card per run.
 * Runs that belong to the same disaster event and purpose are one group. Runs
 * with no event are grouped when they share country, hazard and purpose
 * (e.g. the same baseline analysis run for every region).
 */
export function groupByEvent(runs: Run[]): NotebookGroup[] {
  type Draft = Omit<NotebookGroup, "analyses" | "notebook"> & {
    sortDate: string
    runs: Run[]
  }
  const groups = new Map<string, Draft>()

  for (const run of runs) {
    const purposeKey = run.filters.use_case?.[0]?.key ?? "unknown"
    const hazard = run.filters.hazard?.[0]
    const event = run.stac_item
    const country = run.area?.country_name ?? "Unknown area"

    const key = event
      ? `event:${event.id}:${purposeKey}`
      : `area:${run.area?.country ?? "?"}:${hazard?.key ?? "?"}:${purposeKey}`
    const date = event?.occurred_at ?? run.created_at

    let group = groups.get(key)
    if (!group) {
      group = {
        id: key,
        title: event
          ? event.title
          : hazard
            ? `${hazard.label} · ${country}`
            : country,
        date: formatDate(date),
        kind: event ? "event" : "baseline",
        hazard: hazard?.key ?? null,
        useCase: purposeKey === "unknown" ? null : purposeKey,
        sortDate: date,
        runs: [],
      }
      groups.set(key, group)
    }
    // Non-event groups report their most recent run.
    if (!event && run.created_at > group.sortDate) {
      group.sortDate = run.created_at
      group.date = formatDate(run.created_at)
    }
    group.runs.push(run)
  }

  return [...groups.values()]
    .map((group) => {
      // Newest first, so the "latest" run leads and re-runs read in order.
      const sorted = [...group.runs].sort((a, b) =>
        b.created_at.localeCompare(a.created_at)
      )
      const tags = differingTags(sorted)
      // With a single analysis "same area" is trivially true, so only treat it as
      // meaningful when there are several to tell apart.
      const sameArea =
        sorted.length > 1 &&
        new Set(sorted.map((run) => run.area?.id)).size === 1
      const dayCounts = new Map<string, number>()
      for (const run of sorted) {
        const day = formatDate(run.created_at)
        dayCounts.set(day, (dayCounts.get(day) ?? 0) + 1)
      }

      const analyses: Analysis[] = sorted.map((run, i) => {
        const date = formatDate(run.created_at)
        // When every analysis covers the same area, the run itself is what
        // tells them apart: lead with when it ran, and add the time only if
        // another run shares the day.
        const when =
          sameArea && (dayCounts.get(date) ?? 0) > 1
            ? `${date}, ${formatTime(run.created_at)}`
            : date
        const level =
          !sameArea && run.area
            ? (LEVEL_LABELS[run.area.level] ?? `Level ${run.area.level}`)
            : null
        return {
          id: run.id,
          title: sameArea ? when : (run.area?.name ?? run.title),
          description: [level, ...tags[i], run.summary]
            .filter(Boolean)
            .join(" · "),
          date: sameArea ? null : when,
          url: run.output_url,
        }
      })

      return {
        id: group.id,
        title: group.title,
        notebook: [...new Set(sorted.map((run) => run.notebook_title))].join(
          " / "
        ),
        date: group.date,
        kind: group.kind,
        hazard: group.hazard,
        useCase: group.useCase,
        analyses,
        sortDate: group.sortDate,
      }
    })
    .sort(
      (a, b) =>
        // Disaster events first, then baseline analyses; newest first in each.
        Number(b.kind === "event") - Number(a.kind === "event") ||
        b.sortDate.localeCompare(a.sortDate)
    )
    .map(({ id, title, notebook, date, kind, hazard, useCase, analyses }) => ({
      id,
      title,
      notebook,
      date,
      kind,
      hazard,
      useCase,
      analyses,
    }))
}
