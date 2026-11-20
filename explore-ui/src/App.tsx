import { useMemo, useState } from "react"

import { EventsList } from "@/components/events-list"
import { ExploreFilters } from "@/components/explore-filters"
import { NotebooksMap } from "@/components/notebooks-map"
import { SiteHeader } from "@/components/site-header"
import {
  DEFAULT_FILTERS,
  facetCounts,
  filterOptions,
  filterRuns,
  groupByEvent,
  groupByPlace,
  hasActiveFilters,
  usePublishedNotebooks,
  type Filters,
} from "@/lib/notebooks"

export function App() {
  const { runs, error } = usePublishedNotebooks()
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const options = useMemo(() => filterOptions(runs), [runs])

  const counts = useMemo(() => facetCounts(runs, filters), [runs, filters])

  const visible = useMemo(
    () => (runs ? filterRuns(runs, filters) : null),
    [runs, filters]
  )
  const places = useMemo(
    () => (visible ? groupByPlace(visible) : null),
    [visible]
  )
  const groups = useMemo(
    () => (visible ? groupByEvent(visible) : null),
    [visible]
  )

  return (
    <div className="flex min-h-svh flex-col bg-muted lg:h-svh">
      <SiteHeader current="/" />
      {/* Two panes on wide screens: content scrolls on the left, the map fills
          the right edge. They stack on small screens, map first. */}
      <main className="flex flex-1 flex-col lg:grid lg:min-h-0 lg:grid-cols-12">
        <section
          aria-label="Notebook map"
          className="relative order-first h-[360px] lg:order-last lg:col-span-5 lg:h-auto"
        >
          <NotebooksMap places={places} error={error} />
        </section>
        <div className="lg:col-span-7 lg:overflow-y-auto">
          <div className="flex flex-col gap-6 px-6 py-6 lg:pl-container lg:pr-6">
            <ExploreFilters
              filters={filters}
              options={options}
              counts={counts}
              onChange={(patch) => setFilters({ ...filters, ...patch })}
              onClear={() => setFilters(DEFAULT_FILTERS)}
            />
            <EventsList
              groups={groups}
              error={error}
              filtered={hasActiveFilters(filters)}
              onClear={() => setFilters(DEFAULT_FILTERS)}
            />
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
