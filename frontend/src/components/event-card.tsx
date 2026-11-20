import * as Collapsible from '@radix-ui/react-collapsible';
import { Heading, Link, Text } from '@radix-ui/themes';
import type { ExploreEvent } from '../lib/api';
import { analysisText, eventCardId } from '../lib/explore';

/** Input contract for an event disclosure controlled by the Explore route. */
interface EventCardProps {
  event: ExploreEvent;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Render one event's metadata and a Radix disclosure containing its matching analyses.
 * Heading semantics remain separate from the trigger, and result links use the existing viewer.
 *
 * @param props - Event content, URL-derived open state, and the route's change handler.
 * @param props.event - Event metadata and matching published analyses from Django.
 * @param props.open - Whether this event's analysis links are currently visible.
 * @param props.onOpenChange - Updates the route when the disclosure is toggled.
 * @returns An event card that supports pointer and keyboard expansion.
 */
export function EventCard({ event, open, onOpenChange }: EventCardProps) {
  return (
    <Collapsible.Root asChild open={open} onOpenChange={onOpenChange}>
      <article className='event-card' id={eventCardId(event.id)}>
        <Heading as='h3' size='5' weight='medium' className='event-title'>
          {event.title}
        </Heading>
        <div className='event-meta'>
          <Text as='p' size='2' className='event-purpose'>
            {event.purposes.join(', ')}
          </Text>
          {event.date ? (
            <Text size='2' color='gray' className='event-date' asChild>
              <time dateTime={event.date}>
                {new Date(event.date).toLocaleDateString(undefined, {
                  day: 'numeric',
                  month: 'short',
                  year: 'numeric',
                  timeZone: 'UTC',
                })}
              </time>
            </Text>
          ) : (
            <Text size='2' color='gray' className='event-date'>
              Date unavailable
            </Text>
          )}
        </div>
        <Collapsible.Trigger
          className='event-toggle'
          aria-label={`${open ? 'Collapse' : 'Expand'} ${event.analysis_count} ${event.analysis_count === 1 ? 'analysis' : 'analyses'} for ${event.title}`}
        >
          <span className='event-chevron' aria-hidden='true'>
            <svg
              width='18'
              height='18'
              viewBox='0 0 24 24'
              fill='none'
              stroke='currentColor'
              strokeWidth='1.5'
              strokeLinecap='round'
              strokeLinejoin='round'
            >
              <path d='m9 5 7 7-7 7' />
            </svg>
          </span>
          <Text size='3' weight='bold'>
            {event.analysis_count} {event.analysis_count === 1 ? 'analysis' : 'analyses'}
          </Text>
        </Collapsible.Trigger>
        <Collapsible.Content className='event-content'>
          <ul className='event-analyses'>
            {event.analyses.map((analysis) => {
              const label = analysisText(analysis);
              return (
                <li key={analysis.id}>
                  <Link asChild highContrast underline='none' className='event-analysis-link'>
                    <a href={analysis.viewer_url} aria-label={analysis.title}>
                      <span className='event-analysis-copy'>
                        <Text as='span' size='3' weight='medium' className='event-analysis-title'>
                          {label.title}
                        </Text>
                        {label.location ? (
                          <Text as='span' size='2' color='gray' className='event-analysis-location'>
                            {label.location}
                          </Text>
                        ) : null}
                      </span>
                      <svg
                        width='18'
                        height='18'
                        viewBox='0 0 24 24'
                        fill='none'
                        stroke='currentColor'
                        strokeWidth='1.5'
                        strokeLinecap='round'
                        strokeLinejoin='round'
                        aria-hidden='true'
                      >
                        <path d='m9 5 7 7-7 7' />
                      </svg>
                    </a>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Collapsible.Content>
      </article>
    </Collapsible.Root>
  );
}
