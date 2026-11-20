import { Heading, Text } from '@radix-ui/themes';
import { lazy, Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { ExploreEventsPage, ExploreMapData, ExploreOptions, MapEvent } from '../lib/api';
import { useApi } from '../lib/use-api';
import {
  changeExploreFilter,
  eventCardId,
  exploreEventsUrl,
  exploreMapUrl,
  selectMapEvent,
} from '../lib/explore';
import { Loading, LoadError } from '../components/data-state';
import { EventSidebar } from '../components/event-sidebar';
import { ExploreFilters } from '../components/explore-filters';
import './explore.css';

const ExploreMap = lazy(() =>
  import('../components/explore-map').then((module) => ({ default: module.ExploreMap })),
);

/**
 * Load registered filter choices and select one Montandon source before requesting map and cards.
 *
 * @returns The Explore workspace or a useful configuration/loading error.
 */
export function Explore() {
  const [params] = useSearchParams();
  const catalog = params.get('catalog');
  const options = useApi<ExploreOptions>(
    `/api/runs/explore-options/${catalog ? `?catalog=${encodeURIComponent(catalog)}` : ''}`,
  );
  if (options.status === 'loading')
    return (
      <div className='event-state'>
        <Loading />
      </div>
    );
  if (options.status === 'error')
    return (
      <div className='event-state'>
        <LoadError message={options.message} />
      </div>
    );
  if (!options.data.catalog)
    return (
      <div className='event-state'>
        <Heading as='h1' size='5'>
          Explore analyses
        </Heading>
        <Text as='p'>No active Montandon catalog is available.</Text>
        <a href='/explore'>Reset Explore</a>
      </div>
    );
  return <ExploreWorkspace options={options.data} />;
}

/**
 * Keep map markers, filters, search, card expansion and pagination on the same route state.
 *
 * @param props - Registered controls and the resolved single-catalog configuration.
 * @param props.options - Django-provided choices, including the selected Montandon source.
 * @returns A filtered map and independently scrollable event sidebar.
 */
function ExploreWorkspace({ options }: { options: ExploreOptions }) {
  const [params, setParams] = useSearchParams();
  const effective = new URLSearchParams(params);
  effective.set('catalog', options.catalog!.slug);
  const cards = useApi<ExploreEventsPage>(exploreEventsUrl(effective), true);
  const markers = useApi<ExploreMapData>(exploreMapUrl(effective), true);
  const selected = params.get('selected');
  const [hovered, setHovered] = useState<string | null>(null);
  const cardData = cards.status !== 'error' ? cards.data : undefined;
  const markerData = markers.status !== 'error' ? markers.data : undefined;

  useEffect(() => {
    if (cards.status !== 'success' || !selected) return;
    const card = document.getElementById(eventCardId(selected));
    card?.focus({ preventScroll: true });
    card?.scrollIntoView({
      block: 'nearest',
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    });
  }, [cards, selected]);

  /**
   * Change one card's expansion without changing matching data or losing the current page.
   *
   * @param id - Catalog-scoped event identifier.
   * @param open - Whether its analysis links should be visible.
   */
  function toggleEvent(id: string, open: boolean) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        const expanded = new Set(next.getAll('expanded'));
        if (open) expanded.add(id);
        else expanded.delete(id);
        next.delete('expanded');
        for (const value of expanded) next.append('expanded', value);
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  }

  /**
   * Apply a registered filter to both map and sidebar while resetting pagination.
   *
   * @param key - Country, hazard, purpose, or event-search field.
   * @param value - Selected option or search text; empty clears it.
   */
  function changeFilter(key: string, value: string) {
    setParams(changeExploreFilter(effective, key, value), { preventScrollReset: true });
  }

  /** Reset filters and selection while retaining the current Montandon source. */
  function clearFilters() {
    setParams(new URLSearchParams({ catalog: options.catalog!.slug }), {
      preventScrollReset: true,
    });
  }

  /**
   * Open a picked event, switching sidebar pages first when necessary.
   *
   * @param event - Matching marker and the sidebar page returned by Django.
   */
  function selectEvent(event: MapEvent) {
    if (markers.status !== 'success') return;
    setParams(selectMapEvent(effective, event), { replace: true, preventScrollReset: true });
  }

  return (
    <div className='explore-layout'>
      <Heading as='h1' className='explore-sr-only'>
        Explore analyses
      </Heading>
      <div className='explore-workspace'>
        <ExploreFilters
          options={options}
          params={params}
          onChange={changeFilter}
          onClear={clearFilters}
        />
        <Suspense
          fallback={
            <div className='event-state'>
              <Loading />
            </div>
          }
        >
          <ExploreMap
            events={markerData?.results ?? []}
            selected={selected}
            hovered={hovered}
            loading={markers.status === 'loading'}
            onSelect={selectEvent}
          />
        </Suspense>
        {markers.status === 'error' ? (
          <div className='map-data-error'>
            <LoadError message={markers.message} />
          </div>
        ) : null}
      </div>
      <aside
        className='event-sidebar'
        aria-labelledby={cardData ? 'events-heading' : undefined}
        aria-label={!cardData ? 'Event analyses' : undefined}
        aria-busy={cards.status === 'loading'}
      >
        {cards.status === 'loading' && !cardData ? (
          <div className='event-state'>
            <Loading />
          </div>
        ) : cards.status === 'error' ? (
          <div className='event-state'>
            <LoadError message={cards.message} />
          </div>
        ) : cardData ? (
          <EventSidebar
            data={cardData}
            params={params}
            onToggle={toggleEvent}
            onHover={setHovered}
            loading={cards.status === 'loading'}
          />
        ) : null}
      </aside>
    </div>
  );
}
