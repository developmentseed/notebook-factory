import { useEffect, useState } from 'react';
import { getJson } from './api';

type Result<T> =
  { status: 'loading' } | { status: 'error'; message: string } | { status: 'success'; data: T };

/**
 * Load read-only data for a screen and cancel requests when the URL changes.
 * Results belong to their request URL so a new URL cannot display the old response.
 *
 * @param url - Same-origin Django API path to load.
 * @returns Loading, error, or decoded response state for the current URL.
 */
export function useApi<T>(url: string): Result<T> {
  const [result, setResult] = useState<{ url: string; value: Result<T> }>({
    url,
    value: { status: 'loading' },
  });

  useEffect(() => {
    const controller = new AbortController();
    getJson<T>(url, controller.signal).then(
      (data) => setResult({ url, value: { status: 'success', data } }),
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setResult({
            url,
            value: {
              status: 'error',
              message: error instanceof Error ? error.message : 'Could not load data.',
            },
          });
        }
      },
    );
    return () => controller.abort();
  }, [url]);

  return result.url === url ? result.value : { status: 'loading' };
}
