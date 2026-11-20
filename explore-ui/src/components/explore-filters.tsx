import { useId, type ReactNode } from "react"
import { ChevronDownIcon, SearchIcon, XIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { colourForHazard } from "@/lib/categories"
import {
  FILTER_KEYS,
  FILTER_LABELS,
  hasActiveFilters,
  type FilterKey,
  type FilterOptions,
  type Filters,
  type Option,
} from "@/lib/notebooks"

type Counts = Record<FilterKey, Record<string, number>>

/** A small coloured dot naming a hazard; nothing for other filters. */
function Swatch({ filterKey, value }: { filterKey: FilterKey; value: string }) {
  const colour = filterKey === "hazard" ? colourForHazard(value) : null
  if (!colour) return null
  return (
    <span
      aria-hidden="true"
      className="size-2.5 shrink-0 rounded-full ring-1 ring-foreground/25"
      style={{ backgroundColor: colour }}
    />
  )
}

/** A filter that takes several values: a searchable checklist in a popover. */
function FilterMenu({
  filterKey,
  options,
  counts,
  selected,
  searchable,
  onChange,
}: {
  filterKey: FilterKey
  options: Option[]
  counts: Record<string, number>
  selected: string[]
  searchable?: boolean
  onChange: (values: string[]) => void
}) {
  const label = FILTER_LABELS[filterKey]
  const toggle = (value: string) =>
    onChange(
      selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value]
    )

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="lg"
            data-active={selected.length > 0}
            className="data-[active=true]:border-primary"
          />
        }
      >
        {label}
        {selected.length > 0 && (
          <Badge className="tabular-nums">
            {selected.length}
            <span className="sr-only"> selected</span>
          </Badge>
        )}
        <ChevronDownIcon aria-hidden="true" data-icon="inline-end" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 gap-0 p-0">
        <Command>
          {searchable && (
            <CommandInput placeholder={`Search ${label.toLowerCase()}…`} />
          )}
          <CommandList>
            <CommandEmpty>No {label.toLowerCase()} found.</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = selected.includes(option.value)
                const count = counts[option.value] ?? 0
                return (
                  <CommandItem
                    key={option.value}
                    value={option.label}
                    data-checked={isSelected}
                    // An option that would show nothing can't be added, but a
                    // selected one can always be removed.
                    disabled={count === 0 && !isSelected}
                    onSelect={() => toggle(option.value)}
                  >
                    <span className="flex flex-1 items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <Swatch filterKey={filterKey} value={option.value} />
                        {option.label}
                        {isSelected && (
                          <span className="sr-only"> selected</span>
                        )}
                      </span>
                      <span className="text-muted-foreground tabular-nums">
                        {count}
                      </span>
                    </span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
          {selected.length > 0 && (
            <div className="border-t p-1">
              <Button
                variant="ghost"
                className="w-full justify-start"
                onClick={() => onChange([])}
              >
                Clear {label.toLowerCase()}
              </Button>
            </div>
          )}
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function DisasterSearch({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const id = useId()
  const active = value.trim() !== ""
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant={active ? "default" : "outline"}
            size="icon-lg"
            className="max-sm:w-full max-sm:gap-1.5"
            aria-label={
              active ? `Find a disaster: “${value}”` : "Find a disaster"
            }
          />
        }
      >
        <SearchIcon aria-hidden="true" />
        <span className="sm:hidden">Find a disaster</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <form role="search" onSubmit={(e) => e.preventDefault()}>
          <Label htmlFor={id} className="sr-only">
            Find a disaster
          </Label>
          <Input
            id={id}
            type="search"
            name="q"
            autoComplete="off"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="Find a disaster: GLIDE, EMSR, go:7464…"
          />
        </form>
      </PopoverContent>
    </Popover>
  )
}

/** One applied filter, removable with a click or the keyboard. */
function Pill({
  group,
  label,
  swatch,
  onRemove,
}: {
  group: string
  label: string
  swatch?: ReactNode
  onRemove: () => void
}) {
  return (
    <Badge variant="secondary" className="h-7 gap-1 pr-0.5 pl-2.5 text-xs">
      {swatch}
      <span className="text-foreground/70">{group}</span>
      <span className="font-medium">{label}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${group} filter: ${label}`}
        className="grid size-6 place-items-center rounded-full outline-none hover:bg-foreground/10 focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <XIcon aria-hidden="true" className="size-3" />
      </button>
    </Badge>
  )
}

export function ExploreFilters({
  filters,
  options,
  counts,
  onChange,
  onClear,
}: {
  filters: Filters
  options: FilterOptions
  counts: Counts
  onChange: (patch: Partial<Filters>) => void
  onClear: () => void
}) {
  const labelOf = (key: FilterKey, value: string) =>
    options[key].find((o) => o.value === value)?.label ?? value

  type PillData = {
    id: string
    group: string
    label: string
    swatch?: ReactNode
    remove: () => void
  }
  const pills: PillData[] = FILTER_KEYS.flatMap((key) =>
    filters[key].map((value) => ({
      id: `${key}:${value}`,
      group: FILTER_LABELS[key],
      label: labelOf(key, value),
      swatch: <Swatch filterKey={key} value={value} />,
      remove: () =>
        onChange({ [key]: filters[key].filter((v) => v !== value) }),
    }))
  )
  const search = filters.q.trim()
  if (search) {
    pills.push({
      id: "q",
      group: "Search",
      label: `“${search}”`,
      remove: () => onChange({ q: "" }),
    })
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-3 text-card-foreground shadow-sm">
      <div
        role="group"
        aria-label="Filters"
        className="flex flex-wrap items-center gap-2"
      >
        {FILTER_KEYS.map((key) => (
          <FilterMenu
            key={key}
            filterKey={key}
            options={options[key]}
            counts={counts[key]}
            selected={filters[key]}
            searchable={key === "country"}
            onChange={(values) => onChange({ [key]: values })}
          />
        ))}
        <div className="ml-auto max-sm:w-full">
          <DisasterSearch value={filters.q} onChange={(q) => onChange({ q })} />
        </div>
      </div>
      {hasActiveFilters(filters) && (
        <div
          aria-label="Applied filters"
          className="flex flex-wrap items-center gap-2 border-t pt-3"
        >
          {pills.map((pill) => (
            <Pill
              key={pill.id}
              group={pill.group}
              label={pill.label}
              swatch={pill.swatch}
              onRemove={pill.remove}
            />
          ))}
          <Button variant="ghost" size="lg" onClick={onClear}>
            Clear all
          </Button>
        </div>
      )}
    </div>
  )
}
