import {
  Button,
  Callout,
  Checkbox,
  Heading,
  RadioGroup,
  Select,
  Spinner,
  Text,
  TextField,
} from '@radix-ui/themes';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router';
import { config } from '../lib/config';
import { useRunAreas, type RunConfig } from '../lib/run';
import { DjangoFields, RunEventPicker, RunOptions } from '../components/run-fields';
import { CountryPicker } from '../components/country-picker';
import { Cross2Icon } from '@radix-ui/react-icons';
import './run.css';

const AreaMap = lazy(() =>
  import('../components/area-map').then((module) => ({ default: module.AreaMap })),
);

/** Server-resolved configuration for one authenticated template run. */
interface RunWorkspaceProps {
  run: RunConfig;
}

/**
 * Configure an analysis with one shared selection for names, map and the final run count.
 * Django receives a native CSRF-protected POST and owns validation, permissions and queueing.
 * Bound server errors preserve every selection and field value after an unsuccessful submit.
 *
 * @param props - Server-resolved template, initial selection and existing parameter fields.
 * @param props.run - Django form data and trusted rendered field slots.
 * @returns A complete area, inputs and review workspace using the public app's design tokens.
 */
function RunWorkspace({ run }: RunWorkspaceProps) {
  const [countryId, setCountryId] = useState(run.country);
  const country = run.countries.find((item) => item.iso3 === countryId);
  const [whole, setWhole] = useState(!run.level || Number(run.level) === 0);
  const [regionLevel, setRegionLevel] = useState(Number(run.level) || 1);
  const [choice, setChoice] = useState({
    key: `${run.country}:${Number(run.level) || 0}`,
    ids: run.areaIds.split(',').filter(Boolean).map(Number),
    all: run.allAtLevel,
  });
  const [search, setSearch] = useState('');
  const [retry, setRetry] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const errors = useRef<HTMLDivElement>(null);
  const regionLevels = country?.levels.filter((value) => value > 0) || [];
  const isWhole = Boolean(country?.levels.includes(0) && (whole || !regionLevels.length));
  const level = isWhole
    ? 0
    : regionLevels.includes(regionLevel)
      ? regionLevel
      : (regionLevels[0] ?? 0);
  const key = `${countryId}:${level}`;
  const result = useRunAreas(run.requiresArea ? countryId : '', level, retry);
  const areas = country && !result.loading && !result.error ? result.areas : [];
  const selected = isWhole
    ? areas.slice(0, 1).map((area) => area.id)
    : choice.key === key
      ? areas.filter((area) => choice.all || choice.ids.includes(area.id)).map((area) => area.id)
      : [];
  const selectedSet = new Set(selected);
  const nameCounts = new Map<string, number>();
  for (const area of areas) nameCounts.set(area.name, (nameCounts.get(area.name) || 0) + 1);
  const names = areas.filter((area) => selectedSet.has(area.id));
  const visible = areas.filter((area) =>
    `${area.name} ${area.code}`.toLocaleLowerCase().includes(search.toLocaleLowerCase().trim()),
  );
  const count = run.requiresArea ? selected.length : 1;
  const summary = !run.requiresArea
    ? 'No area selection needed'
    : !country
      ? 'Choose a country to get started'
      : result.loading
        ? `Loading areas in ${country.name}…`
        : isWhole && count
          ? `${country.name}, whole country`
          : count
            ? `${count} ${count === 1 ? 'region' : 'regions'} in ${country.name}`
            : `Choose regions in ${country.name}`;
  useEffect(() => {
    if (run.errors.length) errors.current?.focus();
  }, [run.errors.length]);
  useEffect(() => {
    /** Restore the submit button when a browser back/forward cache restores the form. */
    function restore() {
      setSubmitting(false);
    }
    window.addEventListener('pageshow', restore);
    return () => window.removeEventListener('pageshow', restore);
  }, []);
  /**
   * Toggle only a currently loaded region; stale map features never affect the next country.
   *
   * @param id - Area identifier from the list or map feature.
   */
  function toggle(id: number) {
    if (isWhole || !areas.some((area) => area.id === id)) return;
    setSelectionError('');
    setChoice({
      key,
      all: false,
      ids: selectedSet.has(id) ? selected.filter((value) => value !== id) : [...selected, id],
    });
  }
  return (
    <div className='run-page'>
      <a
        className='run-back'
        href={countryId ? `/build?country=${encodeURIComponent(countryId)}` : '/build'}
      >
        All templates
      </a>
      <header className='run-heading'>
        <Heading as='h1' size='7'>
          {run.title}
        </Heading>

        <a
          href={`/templates/${run.slug}/${countryId ? `?country=${encodeURIComponent(countryId)}` : ''}`}
        >
          View details
        </a>
      </header>
      <form
        method='post'
        action={`/build/${run.slug}/run`}
        className='run-form'
        onSubmit={(event) => {
          if (submitting) {
            event.preventDefault();
            return;
          }
          if (run.requiresArea && !count) {
            event.preventDefault();
            setSelectionError(
              !country
                ? 'Choose a country before running your analysis.'
                : result.error ||
                    (result.loading
                      ? 'Your areas are still loading. Please try again in a moment.'
                      : 'Choose at least one region before running your analysis.'),
            );
            requestAnimationFrame(() => errors.current?.focus());
            return;
          }
          setSubmitting(true);
        }}
      >
        <input type='hidden' name='csrfmiddlewaretoken' value={config.csrfToken} />
        <input type='hidden' name='country' value={countryId} />
        <input type='hidden' name='level' value={level} />
        <input type='hidden' name='area_ids' value={selected.join(',')} />
        {run.stacItem && !run.stacHtml ? (
          <input type='hidden' name='stac_item' value={run.stacItem} />
        ) : null}
        {run.errors.length || selectionError ? (
          <div ref={errors} tabIndex={-1} className='run-error-summary' role='alert'>
            <Heading as='h2' size='3'>
              Check your inputs
            </Heading>
            {selectionError ? (
              <p>{selectionError}</p>
            ) : (
              run.errors.map((error, index) => <p key={index}>{error}</p>)
            )}
          </div>
        ) : null}
        <div className='run-main'>
          {run.requiresArea ? (
            <section className='run-section'>
              <div className='run-section-heading'>
                <Heading as='h2' size='5'>
                  Area
                </Heading>
              </div>
              <CountryPicker
                countries={run.countries}
                country={country}
                onChange={(id) => {
                  if (id === countryId) return;
                  setCountryId(id);
                  setWhole(true);
                  setSearch('');
                  setChoice({ key: '', ids: [], all: false });
                  setSelectionError('');
                }}
              />
              {!run.countries.length ? (
                <Callout.Root color='amber'>
                  <Callout.Text>
                    No compatible areas are available for this template yet. Ask an administrator to
                    import boundaries.
                  </Callout.Text>
                </Callout.Root>
              ) : null}
              {country ? (
                <>
                  {country.levels.includes(0) && regionLevels.length ? (
                    <fieldset className='run-scope'>
                      <legend>Coverage</legend>
                      <RadioGroup.Root
                        value={isWhole ? 'country' : 'regions'}
                        onValueChange={(value) => {
                          setWhole(value === 'country');
                          setSearch('');
                          setSelectionError('');
                        }}
                        orientation='horizontal'
                        size='3'
                      >
                        <RadioGroup.Item value='country'>Whole country</RadioGroup.Item>
                        <RadioGroup.Item value='regions'>Choose regions</RadioGroup.Item>
                      </RadioGroup.Root>
                    </fieldset>
                  ) : null}
                  <div className={`run-area-workspace ${isWhole ? 'run-area-whole' : ''}`}>
                    {!isWhole ? (
                      <div className='run-region-picker'>
                        <div className='run-region-header'>
                          {regionLevels.length > 1 ? (
                            <div className='run-region-field'>
                              <Text as='label' htmlFor='run-level' size='2' weight='medium'>
                                Region level
                              </Text>
                              <Select.Root
                                value={String(level)}
                                size='3'
                                onValueChange={(value) => {
                                  setRegionLevel(Number(value));
                                  setSearch('');
                                  setSelectionError('');
                                }}
                              >
                                <Select.Trigger id='run-level' />
                                <Select.Content>
                                  {regionLevels.map((value) => (
                                    <Select.Item key={value} value={String(value)}>
                                      {value === 1
                                        ? 'Regions (level 1)'
                                        : value === 2
                                          ? 'Regions (level 2)'
                                          : `Local areas (level ${value})`}
                                    </Select.Item>
                                  ))}
                                </Select.Content>
                              </Select.Root>
                            </div>
                          ) : (
                            <Text weight='medium'>Choose regions</Text>
                          )}
                          <div className='run-region-field'>
                            <Text as='label' htmlFor='run-region-search' size='2'>
                              Find a region
                            </Text>
                            <TextField.Root
                              id='run-region-search'
                              size='3'
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' && !event.nativeEvent.isComposing)
                                  event.preventDefault();
                              }}
                              type='search'
                              value={search}
                              onChange={(event) => setSearch(event.target.value)}
                              placeholder='Search by name…'
                            />
                          </div>
                          <div className='run-region-actions'>
                            <Text size='1' color='gray'>
                              {selected.length} of {areas.length} selected
                            </Text>
                            <Button
                              type='button'
                              variant='ghost'
                              size='1'
                              disabled={!areas.length}
                              onClick={() => {
                                setChoice({
                                  key,
                                  all: false,
                                  ids:
                                    selected.length === areas.length
                                      ? []
                                      : areas.map((area) => area.id),
                                });
                                setSelectionError('');
                              }}
                            >
                              {selected.length === areas.length && areas.length
                                ? 'Clear selection'
                                : 'Select all regions'}
                            </Button>
                          </div>
                        </div>
                        <div className='run-region-list' aria-busy={result.loading}>
                          {result.loading ? (
                            <div className='run-region-empty'>
                              <Spinner /> Loading regions…
                            </div>
                          ) : result.error ? (
                            <div className='run-region-empty'>
                              <Text color='red'>{result.error}</Text>
                              <Button
                                type='button'
                                variant='soft'
                                onClick={() => setRetry((value) => value + 1)}
                              >
                                Try again
                              </Button>
                            </div>
                          ) : !visible.length ? (
                            <div className='run-region-empty'>
                              {areas.length
                                ? 'No regions match your search.'
                                : 'No areas are available at this level.'}
                              {search ? (
                                <Button type='button' variant='ghost' onClick={() => setSearch('')}>
                                  Clear search
                                </Button>
                              ) : null}
                            </div>
                          ) : (
                            <fieldset>
                              <legend className='run-sr-only'>Regions in {country.name}</legend>
                              {visible.map((area) => (
                                <label
                                  className='run-region-row'
                                  key={area.id}
                                  data-selected={selectedSet.has(area.id)}
                                >
                                  <Checkbox
                                    checked={selectedSet.has(area.id)}
                                    onCheckedChange={() => toggle(area.id)}
                                  />
                                  <span>
                                    {area.name}
                                    {(nameCounts.get(area.name) || 0) > 1 ? (
                                      <small>{area.code}</small>
                                    ) : null}
                                  </span>
                                </label>
                              ))}
                            </fieldset>
                          )}
                        </div>
                      </div>
                    ) : null}
                    <Suspense
                      fallback={
                        <div className='run-map-loading'>
                          <Spinner /> Loading map preview…
                        </div>
                      }
                    >
                      <AreaMap
                        country={countryId}
                        level={level}
                        areas={areas}
                        selected={selected}
                        onToggle={toggle}
                      />
                    </Suspense>
                  </div>
                  {isWhole && (result.loading || result.error || !count) ? (
                    <div className='run-area-feedback'>
                      <Text size='2' color={result.error ? 'red' : 'gray'}>
                        {result.loading
                          ? 'Loading country boundary…'
                          : result.error || 'No country boundary is available.'}
                      </Text>
                      {result.error ? (
                        <Button
                          type='button'
                          variant='soft'
                          onClick={() => setRetry((value) => value + 1)}
                        >
                          Try again
                        </Button>
                      ) : null}
                    </div>
                  ) : !isWhole && names.length ? (
                    <div className='run-selected-chips' aria-label='Selected regions'>
                      {names.map((area) => (
                        <Button
                          key={area.id}
                          type='button'
                          variant='soft'
                          color='gray'
                          size='1'
                          aria-label={`Remove ${area.name}`}
                          onClick={() => toggle(area.id)}
                        >
                          <span>{area.name}</span> <Cross2Icon aria-hidden='true' />
                        </Button>
                      ))}
                    </div>
                  ) : null}
                </>
              ) : null}
            </section>
          ) : null}
          {run.stacHtml ? (
            <section className='run-section'>
              <div className='run-section-heading'>
                <Heading as='h2' size='5'>
                  Event
                </Heading>
              </div>
              <RunEventPicker html={run.stacHtml} />
            </section>
          ) : null}
          <section className='run-section'>
            <div className='run-section-heading'>
              <Heading as='h2' size='5'>
                {run.hasParameters ? 'Analysis inputs' : 'Options'}
              </Heading>
            </div>
            {run.hasParameters ? <DjangoFields html={run.parametersHtml} /> : null}
            <RunOptions html={run.optionsHtml} initialOpen={run.optionsOpen} />
          </section>
        </div>
        <aside className='run-review'>
          <Heading as='h2' size='4'>
            Run summary
          </Heading>
          <div className='run-review-area' role='status'>
            <Text as='p' weight='medium'>
              {summary}
            </Text>
            {run.requiresArea && !isWhole && names.length ? (
              <Text as='p' size='2' color='gray'>
                {names
                  .slice(0, 3)
                  .map((area) => area.name)
                  .join(', ')}
                {names.length > 3 ? ` and ${names.length - 3} more` : ''}
              </Text>
            ) : null}
          </div>
          {count ? (
            <div className='run-review-count'>
              <Text weight='medium'>
                {count} {count === 1 ? 'analysis' : 'analyses'}
              </Text>
              {run.runtime ? (
                <Text size='2' color='gray'>
                  About {run.runtime} min per analysis
                </Text>
              ) : null}
            </div>
          ) : null}
          {run.requiresArea && !isWhole && count > 0 ? (
            <Text as='p' size='2' color='gray'>
              One analysis per selected region.
            </Text>
          ) : null}
          <Button type='submit' size='3' loading={submitting}>
            {submitting ? 'Starting…' : count > 1 ? `Run ${count} analyses` : 'Run analysis'}
          </Button>
        </aside>
      </form>
    </div>
  );
}

/**
 * Resolve the run bootstrapped by Django; unmatched client-side entry offers a full-navigation link.
 * Run links use full navigation so authentication and prefilled event/area query strings stay intact.
 *
 * @returns The template workspace or a normal link that requests its Django configuration.
 */
export function Run() {
  const { slug } = useParams();
  const run = config.run;
  return run && run.slug === slug ? (
    <RunWorkspace run={run} />
  ) : (
    <div className='empty-state'>
      <Heading>Configure your analysis</Heading>
      <Button asChild>
        <a href={location.pathname + location.search}>Open run form</a>
      </Button>
    </div>
  );
}
