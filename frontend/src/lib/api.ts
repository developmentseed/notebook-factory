/** A page of records returned by Django REST Framework's list endpoints. */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** Template fields used by the Build screen; execution settings stay in Django. */
export interface NotebookTemplate {
  slug: string;
  title: string;
  summary: string;
  requires_area: boolean;
  requires_stac_item: boolean;
}

/** Published run fields needed to display a result card and open its viewer. */
export interface NotebookRun {
  id: string;
  title: string;
  summary: string;
  subtitle: string;
  created_at: string;
}

/**
 * Read JSON from a Django endpoint using the current browser session.
 *
 * @param url - Same-origin API path, including any query parameters.
 * @param signal - Cancels the request when its screen unmounts or inputs change.
 * @returns The decoded API response. Callers supply the expected response type.
 * @throws {Error} When the HTTP response is unsuccessful or JSON cannot be decoded.
 */
export async function getJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    signal,
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(`Could not load data (${response.status}).`);
  return response.json() as Promise<T>;
}
