import { Button, IconButton, Popover, Select, Text, TextField } from '@radix-ui/themes';
import type { FormEvent } from 'react';
import { useState } from 'react';
import type { ExploreOptions } from '../lib/api';

/** Registered filter choices and URL-owned selection handlers. */
interface ExploreFiltersProps {
  options: ExploreOptions;
  params: URLSearchParams;
  onChange: (key: string, value: string) => void;
  onClear: () => void;
}

/**
 * Render the mockup's country, hazard, purpose controls and on-demand event search.
 * Selects update the shared URL; event search submits explicitly so typing never moves the map.
 *
 * @param props - Registered options, current URL state and route-owned change handlers.
 * @param props.options - Existing Django country, hazard and template choices.
 * @param props.params - Current filter selections.
 * @param props.onChange - Applies a filter to both map and sidebar.
 * @param props.onClear - Resets filters within the current Montandon catalog.
 * @returns Labelled Radix controls and an event-search popover.
 */
export function ExploreFilters({ options, params, onChange, onClear }: ExploreFiltersProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const search = params.get('q') || params.get('search') || '';
  const active = ['country', 'hazard', 'purpose', 'notebook', 'template', 'q', 'search'].some(
    (key) => params.has(key),
  );

  /**
   * Apply the event query only when the user submits the search form.
   *
   * @param event - Browser form submission carrying the search text.
   */
  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onChange('q', String(new FormData(event.currentTarget).get('q') || '').trim());
    setSearchOpen(false);
  }

  return (
    <div className='explore-toolbar'>
      <div className='explore-filter country-filter'>
        <Text as='label' htmlFor='explore-country' size='2' weight='medium'>
          Country
        </Text>
        <Select.Root
          value={params.get('country') || 'all'}
          onValueChange={(value) => onChange('country', value === 'all' ? '' : value)}
          name='country'
        >
          <Select.Trigger
            id='explore-country'
            color='gray'
            variant='ghost'
            radius='small'
            aria-label='Country'
            data-active={Boolean(params.get('country'))}
          />
          <Select.Content
            position='popper'
            color='gray'
            variant='soft'
            highContrast
            className='explore-select-menu'
          >
            <Select.Item value='all'>All</Select.Item>
            {options.countries.map((country) => (
              <Select.Item key={country.iso3} value={country.iso3}>
                {country.name}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </div>
      <div className='explore-filter hazard-filter'>
        <Text as='label' htmlFor='explore-hazard' size='2' weight='medium'>
          Hazard
        </Text>
        <Select.Root
          value={params.get('hazard') || 'all'}
          onValueChange={(value) => onChange('hazard', value === 'all' ? '' : value)}
          name='hazard'
        >
          <Select.Trigger
            id='explore-hazard'
            color='gray'
            variant='ghost'
            radius='small'
            aria-label='Hazard'
            data-active={Boolean(params.get('hazard'))}
          />
          <Select.Content
            position='popper'
            color='gray'
            variant='soft'
            highContrast
            className='explore-select-menu'
          >
            <Select.Item value='all'>All</Select.Item>
            {options.hazards.map((hazard) => (
              <Select.Item key={hazard.key} value={hazard.key}>
                {hazard.label}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </div>
      <div className='explore-filter purpose-filter'>
        <Text as='label' htmlFor='explore-purpose' size='2' weight='medium'>
          Purpose
        </Text>
        <Select.Root
          value={params.get('purpose') || params.get('notebook') || params.get('template') || 'all'}
          onValueChange={(value) => onChange('purpose', value === 'all' ? '' : value)}
          name='purpose'
        >
          <Select.Trigger
            id='explore-purpose'
            color='gray'
            variant='ghost'
            radius='small'
            aria-label='Purpose'
            data-active={Boolean(
              params.get('purpose') || params.get('notebook') || params.get('template'),
            )}
          />
          <Select.Content
            position='popper'
            color='gray'
            variant='soft'
            highContrast
            className='explore-select-menu'
          >
            <Select.Item value='all'>All</Select.Item>
            {options.templates.map((template) => (
              <Select.Item key={template.slug} value={template.slug}>
                {template.title}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
      </div>
      <div className='explore-toolbar-actions'>
        <Popover.Root open={searchOpen} onOpenChange={setSearchOpen}>
          <Popover.Trigger>
            <IconButton
              className='explore-search-trigger'
              variant='ghost'
              color='gray'
              data-active={Boolean(search)}
              radius='small'
              aria-label={search ? `Search events: ${search}` : 'Search events'}
              title='Search events'
            >
              <svg
                width='18'
                height='18'
                viewBox='0 0 24 24'
                fill='none'
                stroke='currentColor'
                strokeWidth='1.75'
                strokeLinecap='round'
                aria-hidden='true'
              >
                <circle cx='10.5' cy='10.5' r='6.5' />
                <path d='m16 16 5 5' />
              </svg>
            </IconButton>
          </Popover.Trigger>
          <Popover.Content className='explore-search-popover' align='end' size='2'>
            <search aria-label='Event search'>
              <form action='/explore' method='get' onSubmit={submitSearch}>
                <Text as='label' htmlFor='explore-query' size='2' weight='medium'>
                  Search events
                </Text>
                <TextField.Root
                  id='explore-query'
                  name='q'
                  type='search'
                  defaultValue={search}
                  placeholder='Event name or ID…'
                  autoComplete='off'
                  autoCapitalize='none'
                  spellCheck={false}
                  enterKeyHint='search'
                />
                <Text as='p' size='1' color='gray'>
                  Search {options.catalog?.name} events with published analyses.
                </Text>
                <Button type='submit' size='2' color='gray' highContrast>
                  Search
                </Button>
              </form>
            </search>
          </Popover.Content>
        </Popover.Root>
        {active ? (
          <IconButton
            className='explore-clear'
            variant='ghost'
            color='gray'
            size='2'
            aria-label='Clear filters'
            title='Clear filters'
            onClick={() => {
              onClear();
              document.getElementById('explore-country')?.focus();
            }}
          >
            <svg
              width='16'
              height='16'
              viewBox='0 0 24 24'
              fill='none'
              stroke='currentColor'
              strokeWidth='1.7'
              strokeLinecap='round'
              aria-hidden='true'
            >
              <path d='m6 6 12 12M18 6 6 18' />
            </svg>
          </IconButton>
        ) : null}
      </div>
      {search ? (
        <Text as='p' className='explore-query-summary' size='2'>
          Results for “{search}”
        </Text>
      ) : null}
    </div>
  );
}
