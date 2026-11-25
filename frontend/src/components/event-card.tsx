import * as Collapsible from '@radix-ui/react-collapsible';
import { Badge, Heading, Link, Text } from '@radix-ui/themes';
import type { ExploreEvent } from '../lib/api';
import { analysisRows, eventCardId, formatDate } from '../lib/explore';
import { HazardTiles } from './hazard-tiles';

/** Input contract for an event disclosure controlled by the Explore route. */
interface EventCardProps {
  event: ExploreEvent;
  open: boolean;
  selected?: boolean;
  onOpenChange: (open: boolean) => void;
  onHover?: (id: string | null) => void;
}

/**
 * Render one event's metadata and a Radix disclosure containing its matching analyses.
 * Heading semantics remain separate from the trigger, and result links use the existing viewer.
 *
 * @param props - Event content, URL-derived open state, and the route's change handler.
 * @param props.event - Event metadata and matching published analyses from Django.
 * @param props.open - Whether this event's analysis links are currently visible.
 * @param props.selected - Whether this card corresponds to the selected map marker.
 * @param props.onOpenChange - Updates the route when the disclosure is toggled.
 * @param props.onHover - Highlights this event's map marker on pointer hover or keyboard focus.
 * @returns An event card that supports pointer and keyboard expansion.
 */
export function EventCard({
  event,
  open,
  selected = false,
  onOpenChange,
  onHover,
}: EventCardProps) {
  return (
    <Collapsible.Root asChild open={open} onOpenChange={onOpenChange}>
      <article
        className='event-card'
        id={eventCardId(event.id)}
        tabIndex={-1}
        data-selected={selected}
        onPointerEnter={(pointer) => {
          if (pointer.pointerType !== 'touch') onHover?.(event.id);
        }}
        onPointerLeave={() => onHover?.(null)}
        onFocusCapture={() => onHover?.(event.id)}
        onBlurCapture={(focus) => {
          if (!focus.currentTarget.contains(focus.relatedTarget)) onHover?.(null);
        }}
      >
        <div className='event-header'>
          <HazardTiles hazards={event.hazards} />
          <div className='event-heading'>
            {event.date ? (
              <Text size='2' color='gray' className='event-date' asChild>
                <time dateTime={event.date}>{formatDate(event.date)}</time>
              </Text>
            ) : (
              <Text size='2' color='gray' className='event-date'>
                Date unavailable
              </Text>
            )}
            <Heading as='h3' size='5' weight='medium' className='event-title'>
              {event.title}
            </Heading>
            <ul className='event-purposes' aria-label='Notebooks'>
              {event.purposes.map((purpose) => (
                <li key={purpose}>
                  <Badge
                    className='event-purpose'
                    color='gray'
                    variant='soft'
                    radius='full'
                    size='2'
                    highContrast
                  >
                    {purpose}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
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
          <Text size='2' weight='medium'>
            {event.analysis_count} {event.analysis_count === 1 ? 'analysis' : 'analyses'}
          </Text>
        </Collapsible.Trigger>
        <Collapsible.Content className='event-content'>
          <ul className='event-analyses'>
            {analysisRows(event.analyses).map((row) => (
              <li key={row.id}>
                <Link asChild highContrast underline='none' className='event-analysis-link'>
                  <a href={row.href}>
                    <span className='event-analysis-copy'>
                      {row.context ? <span className='explore-sr-only'>{row.context}</span> : null}
                      <Text as='span' size='3' weight='medium' className='event-analysis-title'>
                        {row.title}
                      </Text>
                      {[row.detail, row.differences, row.summary].map((line, index) =>
                        line ? (
                          <Text
                            key={index}
                            as='span'
                            size='2'
                            color='gray'
                            className='event-analysis-detail'
                          >
                            {line}
                          </Text>
                        ) : null,
                      )}
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
            ))}
          </ul>
        </Collapsible.Content>
      </article>
    </Collapsible.Root>
  );
}
