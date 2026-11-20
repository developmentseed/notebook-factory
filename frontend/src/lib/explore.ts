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
