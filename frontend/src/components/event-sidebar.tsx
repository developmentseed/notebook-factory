import { Button, Heading, Text } from '@radix-ui/themes';
import { Link } from 'react-router';
import type { ExploreEventsPage } from '../lib/api';
import { explorePageUrl } from '../lib/explore';
import { EventCard } from './event-card';

/** Data and URL state needed to display one page of event disclosures. */
interface EventSidebarProps {
  data: ExploreEventsPage;
  params: URLSearchParams;
  onToggle: (id: string, open: boolean) => void;
}

/**
 * Display event cards and matching totals, with pagination over whole events.
 * Several cards can stay open for comparison; the route owns their state for shareable URLs.
 *
 * @param props - The filtered event page and browser query state.
 * @param props.data - Complete event groups for the current page and matching totals.
 * @param props.params - Current filters, pagination, and expanded event identifiers.
 * @param props.onToggle - Changes one event's expansion state in the URL.
 * @returns Event disclosures or an empty state, followed by pagination when needed.
 */
export function EventSidebar({ data, params, onToggle }: EventSidebarProps) {
  const expanded = new Set(params.getAll('expanded'));
  const page = Number(params.get('page') || '1');
  const filtered = [...params.keys()].some((key) => key !== 'expanded' && key !== 'page');

  return (
    <>
      <div className='explore-sr-only'>
        <Heading as='h2' id='events-heading'>
          Event analyses
        </Heading>
        <Text as='p' size='2' color='gray' role='status'>
          {data.count} {data.count === 1 ? 'event' : 'events'}, {data.analysis_count}{' '}
          {data.analysis_count === 1 ? 'analysis' : 'analyses'}
        </Text>
      </div>
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
            <Link to='/explore'>Clear filters</Link>
          ) : (
            <a href='/'>Browse all analyses</a>
          )}
        </div>
      ) : (
        <div className='event-list'>
          {data.results.map((event) => (
            <EventCard
              key={event.id}
              event={event}
              open={expanded.has(event.id)}
              onOpenChange={(open) => onToggle(event.id, open)}
            />
          ))}
        </div>
      )}
      {data.previous || data.next ? (
        <nav className='event-pagination' aria-label='Event pages'>
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
