import { useEffect, useRef } from "react"
import * as maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"

import { colourForHazard } from "@/lib/categories"
import type { NotebookPlace, HazardCount } from "@/lib/notebooks"

const BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"

/** Colour for a hazard the palette doesn't know. */
const NEUTRAL = "var(--muted-foreground)"

const colourVar = (key: string) => colourForHazard(key) ?? NEUTRAL

/** MapLibre can't parse oklch(), so resolve any CSS colour to rgb(). */
function resolveColour(value: string): string {
  const probe = document.createElement("span")
  probe.style.color = value
  document.body.appendChild(probe)
  const resolved = getComputedStyle(probe).color
  probe.remove()
  const ctx = document.createElement("canvas").getContext("2d")!
  ctx.fillStyle = resolved
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `rgb(${r}, ${g}, ${b})`
}

/** Dot radius in CSS px: area grows with the number of notebooks. */
const radiusFor = (count: number) => 5 + 3 * Math.sqrt(count)

const PIXEL_RATIO = 2

/** A dot split into one slice per hazard, sized by notebook count. */
function pieIcon(place: NotebookPlace): ImageData {
  const radius = radiusFor(place.count)
  const ring = 1.5
  const size = Math.ceil((radius + ring + 1) * 2)
  const canvas = document.createElement("canvas")
  canvas.width = canvas.height = size * PIXEL_RATIO
  const ctx = canvas.getContext("2d")!
  ctx.scale(PIXEL_RATIO, PIXEL_RATIO)
  const centre = size / 2

  let start = -Math.PI / 2
  for (const hazard of place.hazards) {
    const end = start + (2 * Math.PI * hazard.count) / place.count
    ctx.beginPath()
    ctx.moveTo(centre, centre)
    ctx.arc(centre, centre, radius, start, end)
    ctx.closePath()
    ctx.fillStyle = resolveColour(colourVar(hazard.key))
    ctx.fill()
    // A hairline between slices keeps neighbouring colours distinct.
    if (place.hazards.length > 1) {
      ctx.strokeStyle = "#ffffff"
      ctx.lineWidth = 1
      ctx.stroke()
    }
    start = end
  }
  ctx.beginPath()
  ctx.arc(centre, centre, radius, 0, 2 * Math.PI)
  ctx.strokeStyle = "#ffffff"
  ctx.lineWidth = ring
  ctx.stroke()
  // A hairline outside the white ring keeps pale colours (yellow) visible on
  // the light basemap.
  ctx.beginPath()
  ctx.arc(centre, centre, radius + ring / 2, 0, 2 * Math.PI)
  ctx.strokeStyle = "rgba(0, 0, 0, 0.35)"
  ctx.lineWidth = 1
  ctx.stroke()

  return ctx.getImageData(0, 0, canvas.width, canvas.height)
}

const iconId = (place: NotebookPlace) =>
  `place-${place.id}-${place.hazards.map((u) => `${u.key}${u.count}`).join("-")}`

/** Register an image for every place the map hasn't drawn yet. */
function ensureIcons(map: maplibregl.Map, places: NotebookPlace[]) {
  for (const place of places) {
    const id = iconId(place)
    if (!map.hasImage(id)) {
      map.addImage(id, pieIcon(place), { pixelRatio: PIXEL_RATIO })
    }
  }
}

function toGeoJSON(places: NotebookPlace[]) {
  return {
    type: "FeatureCollection" as const,
    features: places.map((p) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: p.coordinates },
      properties: {
        icon: iconId(p),
        name: p.name,
        country: p.country,
        count: p.count,
        hazards: JSON.stringify(p.hazards),
      },
    })),
  }
}

/** Frame the notebooks; a fixed world view would stack every dot together. */
function frame(map: maplibregl.Map, places: NotebookPlace[], animate: boolean) {
  if (places.length === 0) return
  const bounds = new maplibregl.LngLatBounds()
  for (const p of places) bounds.extend(p.coordinates)
  map.fitBounds(bounds, {
    padding: 80,
    maxZoom: 5,
    animate,
    duration: 600,
  })
}

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`

/** Popup body, built from text nodes so API strings are never parsed as HTML. */
function popupContent(
  name: string,
  country: string,
  count: number,
  hazards: HazardCount[]
): HTMLElement {
  const el = document.createElement("div")
  el.className = "flex flex-col gap-1 text-xs"
  const title = document.createElement("div")
  title.className = "font-medium"
  title.textContent = name
  const detail = document.createElement("div")
  detail.className = "text-muted-foreground"
  detail.textContent = `${country} · ${plural(count, "notebook", "notebooks")}`
  el.append(title, detail)
  for (const hazard of hazards) {
    const row = document.createElement("div")
    row.className = "flex items-center gap-1.5"
    const dot = document.createElement("span")
    dot.className = "size-2 shrink-0 rounded-full ring-1 ring-foreground/25"
    dot.style.backgroundColor = colourVar(hazard.key)
    const text = document.createElement("span")
    text.textContent = `${hazard.label} · ${hazard.count}`
    row.append(dot, text)
    el.append(row)
  }
  return el
}

export function NotebooksMap({
  places,
  error,
}: {
  places: NotebookPlace[] | null
  error: string | null
}) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const placesRef = useRef(places)
  const framed = useRef(false)

  // Create the map once; filters then swap its data in place.
  useEffect(() => {
    if (!container.current) return

    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAP,
      center: [20, 20],
      zoom: 1.2,
      attributionControl: { compact: true },
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }))

    const popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 14,
    })

    map.on("load", () => {
      const initial = placesRef.current ?? []
      ensureIcons(map, initial)
      map.addSource("notebooks", {
        type: "geojson",
        data: toGeoJSON(initial),
      })
      map.addLayer({
        id: "notebooks",
        type: "symbol",
        source: "notebooks",
        layout: {
          "icon-image": ["get", "icon"],
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
          // Biggest dots underneath, smaller ones on top (MapLibre draws the
          // lowest sort key last, i.e. on top).
          "symbol-sort-key": ["get", "count"],
        },
      })
    })

    map.on("mousemove", "notebooks", (e) => {
      const feature = e.features?.[0]
      if (!feature || feature.geometry.type !== "Point") return
      map.getCanvas().style.cursor = "pointer"
      const { name, country, count, hazards } = feature.properties as {
        name: string
        country: string
        count: number
        hazards: string
      }
      popup
        .setLngLat(feature.geometry.coordinates as [number, number])
        .setDOMContent(popupContent(name, country, count, JSON.parse(hazards)))
        .addTo(map)
    })
    map.on("mouseleave", "notebooks", () => {
      map.getCanvas().style.cursor = ""
      popup.remove()
    })

    return () => {
      map.remove()
      mapRef.current = null
      framed.current = false
    }
  }, [])

  // Follow the filtered data.
  useEffect(() => {
    placesRef.current = places
    const map = mapRef.current
    if (!map || !places) return
    const source = map.getSource("notebooks") as
      maplibregl.GeoJSONSource | undefined
    if (source) {
      ensureIcons(map, places)
      source.setData(toGeoJSON(places))
    }
    if (places.length > 0) {
      frame(map, places, framed.current)
      framed.current = true
    }
  }, [places])

  const total = places?.reduce((sum, p) => sum + p.count, 0) ?? 0

  // The legend lists the hazards currently on the map.
  const legend = (() => {
    const byKey = new Map<string, HazardCount>()
    for (const place of places ?? []) {
      for (const u of place.hazards) {
        const entry = byKey.get(u.key)
        if (entry) entry.count += u.count
        else byKey.set(u.key, { ...u })
      }
    }
    return [...byKey.values()].sort((a, b) => a.label.localeCompare(b.label))
  })()

  return (
    <div className="absolute inset-0 bg-muted">
      <div
        ref={container}
        role="img"
        aria-label={
          places
            ? `Map of ${plural(places.length, "area", "areas")} with ${plural(total, "published notebook", "published notebooks")}`
            : "Map of areas with published notebooks"
        }
        className="h-full w-full"
      />
      {legend.length > 0 && (
        <div
          role="group"
          aria-label="Legend"
          className="absolute bottom-8 left-3 flex flex-col gap-1.5 rounded-md border bg-card p-2.5 text-xs text-card-foreground shadow-sm"
        >
          {legend.map((u) => (
            <div key={u.key} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="size-2.5 shrink-0 rounded-full ring-1 ring-foreground/25"
                style={{ backgroundColor: colourVar(u.key) }}
              />
              <span>{u.label}</span>
              <span className="ml-auto pl-3 text-muted-foreground tabular-nums">
                {u.count}
              </span>
            </div>
          ))}
        </div>
      )}
      {error && (
        <p
          role="alert"
          className="absolute inset-x-4 top-4 rounded-md border border-destructive/30 bg-card px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  )
}
