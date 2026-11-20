import { Button, Heading, Spinner, Text } from '@radix-ui/themes';
import { Link } from 'react-router';
import type { ExploreEventsPage } from '../lib/api';
import { explorePageUrl } from '../lib/explore';
import { EventCard } from './event-card';

/** Data and URL state needed to display one page of event disclosures. */
interface EventSidebarProps {
  data: ExploreEventsPage;
  params: URLSearchParams;
  onToggle: (id: string, open: boolean) => void;
  onHover?: (id: string | null) => void;
  loading?: boolean;
}

/**
 * Display event cards and matching totals, with pagination over whole events.
 * Several cards can stay open for comparison; the route owns their state for shareable URLs.
 *
 * @param props - The filtered event page and browser query state.
 * @param props.data - Complete event groups for the current page and matching totals.
 * @param props.params - Current filters, pagination, and expanded event identifiers.
 * @param props.onToggle - Changes one event's expansion state in the URL.
 * @param props.onHover - Emphasizes the event marker on pointer hover or keyboard focus.
 * @param props.loading - Keeps previous results visible but inactive during a replacement request.
 * @returns Event disclosures or an empty state, followed by pagination when needed.
 */
export function EventSidebar({
  data,
  params,
  onToggle,
  onHover,
  loading = false,
}: EventSidebarProps) {
  const expanded = new Set(params.getAll('expanded'));
  const page = Number(params.get('page') || '1');
  const filtered = [...params.keys()].some(
    (key) => !['expanded', 'page', 'selected', 'catalog'].includes(key),
  );

  return (
    <>
      <header className='event-sidebar-heading'>
        <Heading as='h2' id='events-heading'>
          {data.count} {data.count === 1 ? 'event' : 'events'}
        </Heading>
        <Text as='p' size='2' color='gray' role='status'>
          {loading ? (
            <>
              <Spinner size='1' /> Updating…
            </>
          ) : (
            <>
              {data.analysis_count} {data.analysis_count === 1 ? 'analysis' : 'analyses'}
            </>
          )}
        </Text>
      </header>
      {data.results.length === 0 ? (
        <div className='event-empty'>
          <Heading as='h3' size='4'>
            {filtered ? 'No matching analyses' : 'No event analyses yet'}
          </Heading>
          <Text as='p' size='2' color='gray'>
            {filtered
              ? 'Try changing your filters.'
              : 'Published analyses linked to a Montandon event will appear here.'}
          </Text>
          {filtered ? (
            <Link
              to={
                params.get('catalog')
                  ? `/explore?catalog=${encodeURIComponent(params.get('catalog')!)}`
                  : '/explore'
              }
            >
              Clear filters
            </Link>
          ) : (
            <a href='/'>Browse all analyses</a>
          )}
        </div>
      ) : (
        <div className='event-list' inert={loading}>
          {data.results.map((event) => (
            <EventCard
              key={event.id}
              event={event}
              open={expanded.has(event.id)}
              selected={params.get('selected') === event.id}
              onHover={onHover}
              onOpenChange={(open) => onToggle(event.id, open)}
            />
          ))}
        </div>
      )}
      {data.previous || data.next ? (
        <nav className='event-pagination' aria-label='Event pages' inert={loading}>
          {data.previous ? (
            <Button asChild variant='ghost'>
              <Link to={explorePageUrl(params, page - 1)}>Previous events</Link>
            </Button>
          ) : (
            <span />
          )}
          <Text size='2' color='gray'>
            Page {page}
          </Text>
          {data.next ? (
            <Button asChild variant='ghost'>
              <Link to={explorePageUrl(params, page + 1)}>Next events</Link>
            </Button>
          ) : (
            <span />
          )}
        </nav>
      ) : null}
    </>
  );
}
