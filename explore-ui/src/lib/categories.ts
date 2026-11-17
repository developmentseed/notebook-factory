/**
 * Categorical colour per hazard, keyed by the API's hazard key. Colours are
 * theme tokens (see --category-* in index.css). An unknown key gets no colour,
 * so the UI falls back to neutral rather than guessing.
 */
const HAZARD_SLOT: Record<string, number> = {
  flood: 1,
  cyclone: 2,
  heatwave: 3,
  drought: 4,
  wildfire: 5,
  earthquake: 6,
  landslide: 7,
  tsunami: 8,
  volcano: 9,
}

/** The fill colour for a hazard, or null if it has none. */
export function colourForHazard(key: string | null | undefined): string | null {
  const slot = key ? HAZARD_SLOT[key] : undefined
  return slot ? `var(--category-${slot})` : null
}

/** Icon colour that stays visible on the hazard's fill. */
export function inkForHazard(key: string | null | undefined): string | null {
  const slot = key ? HAZARD_SLOT[key] : undefined
  return slot ? `var(--category-${slot}-ink)` : null
}
