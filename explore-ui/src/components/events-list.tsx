import { Fragment, type CSSProperties } from "react"

import { Grid } from "@/components/container"
import { ChevronRightIcon, LayersIcon } from "lucide-react"

import cyclone from "@/assets/ocha-icons/cyclone.svg"
import drought from "@/assets/ocha-icons/drought.svg"
import earthquake from "@/assets/ocha-icons/earthquake.svg"
import fire from "@/assets/ocha-icons/fire.svg"
import flood from "@/assets/ocha-icons/flood.svg"
import heatwave from "@/assets/ocha-icons/heatwave.svg"
import landslide from "@/assets/ocha-icons/landslide-mudslide.svg"
import tsunami from "@/assets/ocha-icons/tsunami.svg"
import volcano from "@/assets/ocha-icons/volcano.svg"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item"
import { colourForHazard, inkForHazard } from "@/lib/categories"
import { cn } from "@/lib/utils"
import type { NotebookGroup } from "@/lib/notebooks"

// OCHA Humanitarian Icons (CC0). Keys are the notebook API's hazard keys.
const HAZARD_ICONS: Record<string, string> = {
  flood: flood,
  tsunami: tsunami,
  cyclone: cyclone,
  earthquake: earthquake,
  wildfire: fire,
  heatwave: heatwave,
  drought: drought,
  landslide: landslide,
  volcano: volcano,
}

/** Draws an OCHA icon in the surrounding text colour, so it themes like text. */
function HazardIcon({ hazard }: { hazard: string | null }) {
  const src = hazard ? HAZARD_ICONS[hazard] : undefined
  if (!src) return <LayersIcon className="size-4" />
  return (
    <span
      className="block size-5 bg-current"
      style={{
        maskImage: `url("${src}")`,
        WebkitMaskImage: `url("${src}")`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  )
}

function EventItem({
  group,
  defaultOpen,
}: {
  group: NotebookGroup
  defaultOpen: boolean
}) {
  const count = group.analyses.length
  const isEvent = group.kind === "event"
  // The hazard colour: an event is outlined in it, every card's tile wears it.
  const colour = colourForHazard(group.hazard)
  const ink = inkForHazard(group.hazard)

  return (
    <Collapsible
      defaultOpen={defaultOpen}
      style={
        colour
          ? ({ "--category": colour, "--category-ink": ink } as CSSProperties)
          : undefined
      }
      className={cn(
        "overflow-hidden rounded-lg border bg-card text-card-foreground",
        isEvent
          ? "border-(--category,var(--primary)) shadow-sm"
          : "border-foreground/20"
      )}
    >
      <h3>
        <Item
          size="default"
          className="group rounded-none border-0 text-left hover:bg-muted"
          render={<CollapsibleTrigger />}
        >
          <ItemMedia
            aria-hidden="true"
            className={cn(
              "size-10 rounded-md",
              colour
                ? "bg-(--category) text-(--category-ink)"
                : "bg-secondary text-secondary-foreground"
            )}
          >
            <HazardIcon hazard={group.hazard} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle className="line-clamp-none flex-wrap gap-x-2 text-sm">
              {group.title}
            </ItemTitle>
            <ItemDescription className="flex flex-wrap items-center gap-1.5">
              {group.notebook.split(" / ").map((title) => (
                <Badge key={title} variant="outline" className="h-6 text-xs">
                  {title}
                </Badge>
              ))}
              <Badge
                variant="outline"
                className="h-6 text-xs tabular-nums text-muted-foreground"
              >
                {group.date}
              </Badge>
            </ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant="secondary" className="gap-1 tabular-nums">
              {count} {count === 1 ? "analysis" : "analyses"}
              <ChevronRightIcon
                aria-hidden="true"
                className="size-3 transition-transform duration-200 ease-out group-data-[panel-open]:rotate-90 motion-reduce:transition-none"
              />
            </Badge>
          </ItemActions>
        </Item>
      </h3>
      <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-200 ease-out data-[ending-style]:h-0 data-[starting-style]:h-0 motion-reduce:transition-none">
        <ItemSeparator className="my-0" />
        <ItemGroup className="p-1">
          {group.analyses.map((analysis, i) => (
            <Fragment key={analysis.id}>
              {i > 0 && <ItemSeparator className="my-0" />}
              <Item
                size="xs"
                className="group/row"
                render={
                  <a href={analysis.url} target="_blank" rel="noreferrer" />
                }
              >
                <ItemContent>
                  <ItemTitle className="tabular-nums">
                    {analysis.title}
                  </ItemTitle>
                  {analysis.description && (
                    <ItemDescription>{analysis.description}</ItemDescription>
                  )}
                </ItemContent>
                <ItemActions className="text-muted-foreground">
                  {analysis.date && (
                    <span className="tabular-nums">{analysis.date}</span>
                  )}
                  <ChevronRightIcon
                    aria-hidden="true"
                    className="size-3.5 transition-transform duration-200 ease-out group-hover/row:translate-x-0.5 motion-reduce:transition-none"
                  />
                </ItemActions>
              </Item>
            </Fragment>
          ))}
        </ItemGroup>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function EventsList({
  groups,
  error,
  filtered,
  onClear,
}: {
  groups: NotebookGroup[] | null
  error: string | null
  filtered: boolean
  onClear: () => void
}) {
  const total = groups?.reduce((sum, g) => sum + g.analyses.length, 0) ?? 0
  const empty = groups !== null && groups.length === 0
  return (
    <section aria-label="Events" className="flex flex-col gap-3">
      <div>
        <h2 className="font-heading text-base font-semibold text-foreground">
          Events
        </h2>
        <p className="text-sm text-foreground/70" aria-live="polite">
          {error
            ? error
            : groups
              ? `${groups.length} ${groups.length === 1 ? "event" : "events"} · ${total} ${total === 1 ? "analysis" : "analyses"}`
              : "Loading events…"}
        </p>
      </div>
      {empty && (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-foreground/30 bg-card p-4 text-sm">
          <p>No analyses match these filters.</p>
          {filtered && (
            <Button variant="outline" onClick={onClear}>
              Clear filters
            </Button>
          )}
        </div>
      )}
      <Grid className="items-start gap-3">
        {groups?.map((group, i) => (
          <div key={group.id} className="col-span-full">
            <EventItem group={group} defaultOpen={i === 0} />
          </div>
        ))}
      </Grid>
    </section>
  )
}
