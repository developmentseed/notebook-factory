import { useEffect, useState } from 'react';
import { getJson, type Paginated } from './api';

/** Available country and the template-compatible boundary levels stored for it. */
export interface RunCountry {
  iso3: string;
  name: string;
  levels: number[];
}

/** Django-owned run configuration, including autoescaped parameter and metadata field slots. */
export interface RunConfig {
  slug: string;
  title: string;
  requiresArea: boolean;
  countries: RunCountry[];
  country: string;
  level: number | string | null;
  areaIds: string;
  allAtLevel: boolean;
  optionsOpen: boolean;
  runtime: number | null;
  errors: string[];
  hasParameters: boolean;
  parametersHtml: string;
  optionsHtml: string;
  stacHtml: string | null;
  stacItem: string;
}

/** Lightweight area used for list selection even when map geometry is unavailable. */
export interface RunArea {
  id: number;
  name: string;
  code: string;
  bbox: [number, number, number, number] | null;
}

/** Loading and failure state scoped to a single country and level. */
interface AreaResult {
  url: string;
  areas: RunArea[];
  loading: boolean;
  error: string;
}

/**
 * Load every page of area names; the geometry endpoint's display limit never limits selection.
 * Requests are cancelled and stale results hidden when the country or boundary level changes.
 *
 * @param country - Selected ISO3 code.
 * @param level - Template-compatible boundary level.
 * @param retry - Increment to retry a failed request.
 * @returns Current area choices, loading flag and a recoverable error.
 */
export function useRunAreas(country: string, level: number, retry: number) {
  const url = `/api/areas/?country=${encodeURIComponent(country)}&level=${level}&ordering=name`;
  const requestKey = `${url}:${retry}`;
  const [result, setResult] = useState<AreaResult>({
    url: '',
    areas: [],
    loading: true,
    error: '',
  });
  useEffect(() => {
    if (!country) return;
    const controller = new AbortController();
    /** Read all pages sequentially, preserving the API's complete ordered list. */
    async function read() {
      const areas: RunArea[] = [];
      let next: string | null = url;
      try {
        while (next) {
          const page: Paginated<RunArea> = await getJson(next, controller.signal);
          areas.push(...page.results);
          next = page.next
            ? new URL(page.next, location.origin).pathname +
              new URL(page.next, location.origin).search
            : null;
        }
        if (!controller.signal.aborted)
          setResult({ url: requestKey, areas, loading: false, error: '' });
      } catch {
        if (!controller.signal.aborted)
          setResult({
            url: requestKey,
            areas: [],
            loading: false,
            error: 'We couldn’t load these areas. Please try again.',
          });
      }
    }
    void read();
    return () => controller.abort();
  }, [url, country, requestKey]);
  if (!country) return { url, areas: [], loading: false, error: '' };
  return result.url === requestKey ? result : { url, areas: [], loading: true, error: '' };
}
