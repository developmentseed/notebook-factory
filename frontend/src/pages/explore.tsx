import { Heading } from '@radix-ui/themes';
import { useSearchParams } from 'react-router';
import type { ExploreEventsPage } from '../lib/api';
import { useApi } from '../lib/use-api';
import { exploreEventsUrl } from '../lib/explore';
import { Loading, LoadError } from '../components/data-state';
import { EventSidebar } from '../components/event-sidebar';
import './explore.css';

/**
 * Display the Explore workspace and its independently scrollable event sidebar.
 * Filters come from the URL so future map controls and the sidebar use the same query.
 * Expanded cards are URL state but do not change the API request or matching counts.
 *
 * @returns The event-discovery layout with published analyses grouped by Montandon event.
 */
export function Explore() {
  const [params, setParams] = useSearchParams();
  const result = useApi<ExploreEventsPage>(exploreEventsUrl(params));

  /**
   * Change one card's expansion state while preserving filters and the current event page.
   *
   * @param id - Catalog-scoped event identifier from the API.
   * @param open - Whether the event's published analyses should be visible.
   */
  function toggleEvent(id: string, open: boolean): void {
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

  return (
    <div className='explore-layout'>
      <Heading as='h1' className='explore-sr-only'>
        Explore analyses
      </Heading>
      <div className='explore-workspace' aria-hidden='true' />
      <aside
        className='event-sidebar'
        aria-labelledby={result.status === 'success' ? 'events-heading' : undefined}
        aria-label={result.status !== 'success' ? 'Event analyses' : undefined}
      >
        {result.status === 'loading' ? (
          <div className='event-state'>
            <Loading />
          </div>
        ) : result.status === 'error' ? (
          <div className='event-state'>
            <LoadError message={result.message} />
          </div>
        ) : (
          <EventSidebar data={result.data} params={params} onToggle={toggleEvent} />
        )}
      </aside>
    </div>
  );
}
