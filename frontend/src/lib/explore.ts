import type { EventAnalysis } from './api';

/**
 * Translate Explore URL filters to the existing run API vocabulary.
 * Expansion state stays in the browser URL and never triggers a data request.
 *
 * @param params - Current Explore query parameters, including repeated facet values.
 * @returns The event-card endpoint with only its supported filter parameters.
 */
export function exploreEventsUrl(params: URLSearchParams): string {
  const query = new URLSearchParams();
  const aliases: Record<string, string> = {
    q: 'search',
    purpose: 'notebook',
    template: 'notebook',
  };
  const supported = new Set([
    'country',
    'hazard',
    'use_case',
    'notebook',
    'catalog',
    'search',
    'since',
    'until',
    'page',
  ]);

  for (const [key, value] of params) {
    const target = aliases[key] ?? key;

    if (supported.has(target) && value.trim()) query.append(target, value);
  }

  return `/api/runs/events/?${query}`;
}

/**
 * Build an Explore pagination link without losing filters or expanded-card state.
 *
 * @param params - Current browser query parameters.
 * @param page - One-based event page to display.
 * @returns A React route URL that loads the requested event page.
 */
export function explorePageUrl(params: URLSearchParams, page: number): string {
  const next = new URLSearchParams(params);

  if (page === 1) next.delete('page');
  else next.set('page', String(page));

  return `/explore${next.size ? `?${next}` : ''}`;
}

/**
 * Produce a DOM ID for a catalog-scoped event, including IDs containing spaces or slashes.
 *
 * @param id - Stable event identifier returned by the API.
 * @returns The event card's anchor, also usable by future marker-selection interactions.
 */
export function eventCardId(id: string): string {
  return `event-${encodeURIComponent(id)}`;
}

/**
 * Separate the location from Django's generated template-and-area titles.
 * Custom titles stay intact; only the known purpose prefix is split into two visual levels.
 *
 * @param analysis - Published analysis with its template purpose and existing display title.
 * @returns The primary result label and optional location displayed beneath it.
 */
export function analysisText(analysis: EventAnalysis): { title: string; location: string } {
  const prefix = `${analysis.purpose} \u00b7 `;

  return analysis.title.startsWith(prefix)
    ? { title: analysis.purpose, location: analysis.title.slice(prefix.length) }
    : { title: analysis.title, location: '' };
}

/**
 * Build the marker request with exactly the sidebar's filters, excluding its page number.
 *
 * @param params - Current Explore URL state with the selected catalog applied.
 * @returns An API URL for all matching event locations.
 */
export function exploreMapUrl(params: URLSearchParams): string {
  const url = new URL(exploreEventsUrl(params), 'http://localhost');

  url.searchParams.delete('page');

  return `/api/runs/event-map/?${url.searchParams}`;
}

/**
 * Change a filter without carrying a stale page or marker selection into the new results.
 *
 * @param params - Current route state.
 * @param key - Country, hazard, purpose, or event-search filter to change.
 * @param value - Selected value; an empty string clears this filter.
 * @returns The next URL state, retaining other filters and the catalog.
 */
export function changeExploreFilter(
  params: URLSearchParams,
  key: string,
  value: string,
): URLSearchParams {
  const next = new URLSearchParams(params);

  if (key === 'purpose') {
    next.delete('notebook');
    next.delete('template');
  }

  if (key === 'q') next.delete('search');

  if (value) next.set(key, value);
  else next.delete(key);

  for (const state of ['page', 'selected', 'expanded']) next.delete(state);

  return next;
}

/**
 * Select a map event and navigate to the sidebar page containing its card.
 *
 * @param params - Current filters and expanded cards.
 * @param event - Marker identity and one-based sidebar page returned by Django.
 * @param event.id - Catalog-scoped event key.
 * @param event.page - Sidebar page that contains this event.
 * @returns URL state that expands and selects the marker's corresponding event card.
 */
export function selectMapEvent(
  params: URLSearchParams,
  event: { id: string; page: number },
): URLSearchParams {
  const next = new URLSearchParams(params);

  next.set('selected', event.id);
  if (!next.getAll('expanded').includes(event.id)) next.append('expanded', event.id);

  if (event.page === 1) next.delete('page');
  else next.set('page', String(event.page));

  return next;
}

/**
 * Scale circle area with the matching analysis count while retaining a usable click target.
 *
 * @param count - Number of matching analyses for an event.
 * @returns Marker radius in CSS pixels, bounded for readability at every zoom level.
 */
export function markerRadius(count: number): number {
  return Math.min(22, 6 + Math.sqrt(Math.max(0, count)) * 3);
}
