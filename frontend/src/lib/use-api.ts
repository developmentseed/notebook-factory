import { useEffect, useState } from 'react';
import { getJson } from './api';

/** Request state; loading may carry explicitly retained data from the preceding URL. */
type Result<T> =
  | { status: 'loading'; data?: T }
  | { status: 'error'; message: string }
  | { status: 'success'; data: T };

/**
 * Load read-only data for a screen and cancel requests when the URL changes.
 * Results belong to their request URL. Screens may explicitly retain the last successful
 * response during a replacement request, while still receiving a loading status.
 *
 * @param url - Same-origin Django API path to load.
 * @param keepPreviousData - Preserve the previous response only while the next request loads.
 * @returns Loading, error, or decoded response state for the current URL.
 */
export function useApi<T>(url: string, keepPreviousData = false): Result<T> {
  const [result, setResult] = useState<{ url: string; value: Result<T> }>({
    url,
    value: { status: 'loading' },
  });

  useEffect(() => {
    const controller = new AbortController();
    getJson<T>(url, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ url, value: { status: 'success', data } });
      },
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

  if (result.url === url) return result.value;
  return {
    status: 'loading',
    data: keepPreviousData && result.value.status === 'success' ? result.value.data : undefined,
  };
}
