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

/** One analysis link's visible text inside an expanded event card. */
export interface AnalysisRow {
  id: string;
  href: string;
  title: string;
  detail: string;
  /** Parameters and notebook version that differ from other runs of the same notebook. */
  differences: string;
  summary: string;
  /** Notebook title announced to screen readers when the visible title is the run date. */
  context: string;
}

/**
 * Format a timestamp as a short calendar date in UTC, in the visitor's locale.
 *
 * @param value - ISO 8601 timestamp from the API.
 * @returns Day, abbreviated month and year.
 */
export function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

/**
 * List what sets one run apart from the other runs of its notebook on the same card.
 * Settings shared by all of them are left out, since they do not help to choose between runs.
 *
 * @param analysis - The run being described.
 * @param analyses - Every analysis on the card, including this one.
 * @returns Differing parameters as "label: value", then the version if versions differ.
 */
function runDifferences(analysis: EventAnalysis, analyses: EventAnalysis[]): string {
  const peers = analyses.filter((peer) => peer.purpose === analysis.purpose);
  const parts = analysis.parameters
    .filter((parameter) => {
      const values = peers.map(
        (peer) => peer.parameters.find((other) => other.name === parameter.name)?.value,
      );

      return new Set(values).size > 1;
    })
    .map((parameter) => `${parameter.label}: ${parameter.value}`);
  const versions = new Set(peers.map((peer) => peer.template_version));

  if (versions.size > 1 && analysis.template_version !== null)
    parts.push(`Version ${analysis.template_version}`);

  return parts.join('; ');
}

/**
 * Describe each analysis so that re-runs can be told apart.
 * When every row has the same notebook and area, the run date leads, because the notebook is
 * already named on the card. The time is added only for matching rows that ran on the same day.
 * Admin levels appear only when the card's analyses cover different areas.
 *
 * @param analyses - One event's published analyses, in display order.
 * @returns Link text for each analysis, keeping the given order.
 */
export function analysisRows(analyses: EventAnalysis[]): AnalysisRow[] {
  const rows = analyses.map((analysis) => {
    const text = analysisText(analysis);
    const area = analysis.area?.name ?? '';
    // Custom titles carry no area suffix, so take the area from the API unless the title names it.
    const location = text.location || (text.title.includes(area) ? '' : area);
    const label = { title: text.title, location };
    const day = formatDate(analysis.created_at);
    const subject = JSON.stringify([label.title, label.location]);

    return { analysis, label, day, subject };
  });
  const repeated = rows.length > 1 && new Set(rows.map((row) => row.subject)).size === 1;
  const areas = new Set(analyses.map(({ area }) => JSON.stringify([area?.name, area?.level])));

  return rows.map(({ analysis, label, day, subject }) => {
    const sharesDay = rows.filter((row) => row.subject === subject && row.day === day).length > 1;
    const time = new Date(analysis.created_at).toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    });
    const run = `Run ${day}${sharesDay ? `, ${time} UTC` : ''}`;
    const level = areas.size > 1 ? analysis.area?.level_label : '';
    const detail = [label.location, level, repeated ? '' : run].filter(Boolean).join(' \u00b7 ');

    return {
      id: analysis.id,
      href: analysis.viewer_url,
      title: repeated ? run : label.title,
      detail,
      differences: runDifferences(analysis, analyses),
      summary: analysis.summary,
      context: repeated ? label.title : '',
    };
  });
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
