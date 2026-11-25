import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { createServer } from 'vite';

const server = await createServer({
  server: { middlewareMode: true, ws: false },
  appType: 'custom',
});
after(() => server.close());
const {
  exploreEventsUrl,
  explorePageUrl,
  eventCardId,
  analysisText,
  exploreMapUrl,
  changeExploreFilter,
  selectMapEvent,
  markerRadius,
  analysisRows,
} = await server.ssrLoadModule('/src/lib/explore.ts');
const { EventCard } = await server.ssrLoadModule('/src/components/event-card.tsx');
const { EventSidebar } = await server.ssrLoadModule('/src/components/event-sidebar.tsx');

const event = {
  id: '1:corr:nepal/flood',
  title: 'Flood in Nepal 2026',
  catalog: 'montandon',
  date: '2026-08-25T00:00:00Z',
  purposes: ['Post-disaster impact'],
  hazards: [{ key: 'flood', label: 'Flood' }],
  analysis_count: 1,
  analyses: [
    {
      id: 'analysis-1',
      title: 'Impact estimate',
      purpose: 'Post-disaster impact',
      created_at: '2026-08-26T00:00:00Z',
      viewer_url: '/runs/analysis-1/view/',
      area: null,
      template_version: 1,
      summary: '',
      parameters: [],
    },
  ],
};

test('maps URL filters to the event API and retains repeated hazards', () => {
  const params = new URLSearchParams(
    'country=NPL&hazard=flood&hazard=cyclone&purpose=impact&q=Nepal',
  );
  const query = new URL(exploreEventsUrl(params), 'http://localhost').searchParams;
  assert.equal(query.get('country'), 'NPL');
  assert.equal(query.get('notebook'), 'impact');
  assert.equal(query.get('search'), 'Nepal');
  assert.deepEqual(query.getAll('hazard'), ['flood', 'cyclone']);
});

test('expanding a card does not change the API request', () => {
  const before = new URLSearchParams('country=NPL');
  const after = new URLSearchParams(before);
  after.append('expanded', event.id);
  assert.equal(exploreEventsUrl(before), exploreEventsUrl(after));
});

test('pagination preserves filters and disclosure state', () => {
  const params = new URLSearchParams('country=NPL&expanded=event-a&page=2');
  const next = new URL(explorePageUrl(params, 3), 'http://localhost').searchParams;
  assert.equal(next.get('page'), '3');
  assert.equal(next.get('country'), 'NPL');
  assert.equal(next.get('expanded'), 'event-a');
  assert.equal(
    new URL(explorePageUrl(params, 1), 'http://localhost').searchParams.has('page'),
    false,
  );
});

test('event anchors safely encode catalog identifiers', () => {
  assert.equal(eventCardId('1:corr:nepal flood'), 'event-1%3Acorr%3Anepal%20flood');
});

test('expanded cards show event metadata and real viewer links', () => {
  const html = renderToStaticMarkup(
    React.createElement(EventCard, { event, open: true, onOpenChange: () => {} }),
  );
  assert.match(html, /Flood in Nepal 2026/);
  assert.match(html, /Post-disaster impact/);
  assert.match(html, /2026-08-25/);
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /href="\/runs\/analysis-1\/view\/"/);
  assert.match(html, /1 analysis/);
});

test('collapsed cards hide their analyses and expose disclosure state', () => {
  const html = renderToStaticMarkup(
    React.createElement(EventCard, { event, open: false, onOpenChange: () => {} }),
  );
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /href="\/runs\/analysis-1\/view\/"/);
});

test('refreshing results retains analysis links but prevents interaction with stale results', () => {
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(EventSidebar, {
        data: {
          count: 1,
          analysis_count: 1,
          next: '/api/runs/events/?page=2',
          previous: null,
          results: [event],
        },
        params: new URLSearchParams({ expanded: event.id }),
        onToggle: () => {},
        loading: true,
      }),
    ),
  );
  assert.match(html, /Updating…/);
  assert.match(html, /href="\/runs\/analysis-1\/view\/"/);
  assert.match(html, /class="event-list" inert=""/);
  assert.match(html, /aria-label="Event pages" inert=""/);
});

test('missing event dates are explicit instead of displaying an invalid date', () => {
  const html = renderToStaticMarkup(
    React.createElement(EventCard, {
      event: { ...event, date: null },
      open: false,
      onOpenChange: () => {},
    }),
  );
  assert.match(html, /Date unavailable/);
  assert.doesNotMatch(html, /Invalid Date/);
});

test('filtered empty results offer a way to clear the filters', () => {
  const data = { count: 0, analysis_count: 0, next: null, previous: null, results: [] };
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(EventSidebar, {
        data,
        params: new URLSearchParams('hazard=flood'),
        onToggle: () => {},
      }),
    ),
  );
  assert.match(html, /No matching analyses/);
  assert.match(html, /href="\/explore"/);
  assert.match(html, /Clear filters/);
});

test('an empty catalog explains event linking and keeps existing analyses discoverable', () => {
  const data = { count: 0, analysis_count: 0, next: null, previous: null, results: [] };
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(EventSidebar, {
        data,
        params: new URLSearchParams(),
        onToggle: () => {},
      }),
    ),
  );
  assert.match(html, /No event analyses yet/);
  assert.match(html, /Browse all analyses/);
  assert.doesNotMatch(html, /Clear filters/);
});

test('generated analysis titles separate purpose from location', () => {
  assert.deepEqual(
    analysisText({ ...event.analyses[0], title: 'Post-disaster impact \u00b7 Central' }),
    {
      title: 'Post-disaster impact',
      location: 'Central',
    },
  );
});

test('custom analysis titles remain complete', () => {
  assert.deepEqual(analysisText({ ...event.analyses[0], title: 'Assessment for Central' }), {
    title: 'Assessment for Central',
    location: '',
  });
});

const rerun = (id, createdAt, title = 'Post-disaster impact \u00b7 Nepal', extra = {}) => ({
  id,
  title,
  purpose: 'Post-disaster impact',
  created_at: createdAt,
  viewer_url: `/runs/${id}/view/`,
  area: { name: title.split(' \u00b7 ')[1], level: 1, level_label: 'Admin 1' },
  template_version: 1,
  summary: '',
  parameters: [],
  ...extra,
});

test('re-runs of one notebook and area are led by their run date', () => {
  const rows = analysisRows([
    rerun('b', '2026-09-03T10:00:00Z'),
    rerun('a', '2026-09-02T10:00:00Z'),
  ]);
  assert.match(rows[0].title, /^Run .*3/);
  assert.notEqual(rows[0].title, rows[1].title);
  assert.equal(rows[0].detail, 'Nepal');
  assert.equal(rows[0].context, 'Post-disaster impact');
  assert.doesNotMatch(rows[0].title, /UTC/);
});

test('re-runs on the same day add the time', () => {
  const rows = analysisRows([
    rerun('b', '2026-09-02T14:24:00Z'),
    rerun('a', '2026-09-02T14:21:00Z'),
  ]);
  assert.match(rows[0].title, /UTC$/);
  assert.notEqual(rows[0].title, rows[1].title);
});

test('analyses of different areas keep their title and add the run date below', () => {
  const rows = analysisRows([
    rerun('b', '2026-09-02T14:24:00Z'),
    rerun('a', '2026-09-02T14:21:00Z', 'Post-disaster impact \u00b7 Central'),
  ]);
  assert.equal(rows[0].title, 'Post-disaster impact');
  assert.match(rows[0].detail, /^Nepal \u00b7 Admin 1 \u00b7 Run /);
  assert.doesNotMatch(rows[0].detail, /UTC/);
  assert.equal(rows[0].context, '');
});

test('rows show only the parameters and versions that differ between re-runs', () => {
  const settings = (steps) => [
    { name: 'source', label: 'Population data', value: 'WorldPop' },
    { name: 'steps', label: 'Time steps', value: steps },
  ];
  const rows = analysisRows([
    rerun('b', '2026-09-03T10:00:00Z', undefined, {
      parameters: settings('2'),
      template_version: 2,
      summary: 'Updated extent',
    }),
    rerun('a', '2026-09-02T10:00:00Z', undefined, { parameters: settings('3') }),
  ]);
  assert.equal(rows[0].differences, 'Time steps: 2; Version 2');
  assert.equal(rows[1].differences, 'Time steps: 3; Version 1');
  assert.equal(rows[0].summary, 'Updated extent');
});

test('different notebooks are not compared with each other', () => {
  const rows = analysisRows([
    rerun('b', '2026-09-03T10:00:00Z', undefined, {
      parameters: [{ name: 'top', label: 'Top areas', value: '10' }],
    }),
    rerun('a', '2026-09-02T10:00:00Z', undefined, { purpose: 'Response', template_version: 4 }),
  ]);
  assert.deepEqual(
    rows.map((row) => row.differences),
    ['', ''],
  );
});

test('event cards name every hazard and show a neutral tile when none is tagged', () => {
  const render = (hazards) =>
    renderToStaticMarkup(
      React.createElement(EventCard, {
        event: { ...event, hazards },
        open: false,
        onOpenChange: () => {},
      }),
    );
  const tagged = render([
    { key: 'flood', label: 'Flood' },
    { key: 'custom', label: 'Dust storm' },
  ]);
  assert.match(tagged, /data-hazard="flood"/);
  assert.match(tagged, /Dust storm/);
  assert.match(render([]), /class="hazard-tiles" aria-hidden="true"/);
});

test('map uses sidebar filters without pagination or selection state', () => {
  const params = new URLSearchParams(
    'catalog=montandon&country=NPL&hazard=flood&purpose=impact&q=Nepal&page=3&selected=e&expanded=e',
  );
  const url = new URL(exploreMapUrl(params), 'http://localhost');
  assert.equal(url.pathname, '/api/runs/event-map/');
  assert.equal(url.searchParams.get('catalog'), 'montandon');
  assert.equal(url.searchParams.get('country'), 'NPL');
  assert.equal(url.searchParams.get('notebook'), 'impact');
  assert.equal(url.searchParams.get('search'), 'Nepal');
  for (const key of ['page', 'selected', 'expanded'])
    assert.equal(url.searchParams.has(key), false);
});

test('changing a filter resets page and selection but retains other filters', () => {
  const next = changeExploreFilter(
    new URLSearchParams('catalog=montandon&country=NPL&hazard=flood&page=3&selected=e&expanded=e'),
    'hazard',
    'earthquake',
  );
  assert.equal(next.get('hazard'), 'earthquake');
  assert.equal(next.get('country'), 'NPL');
  assert.equal(next.get('catalog'), 'montandon');
  for (const key of ['page', 'selected', 'expanded']) assert.equal(next.has(key), false);
});

test('changing purpose or search clears legacy aliases', () => {
  assert.equal(
    changeExploreFilter(new URLSearchParams('notebook=old&template=old'), 'purpose', 'impact').has(
      'notebook',
    ),
    false,
  );
  assert.equal(
    changeExploreFilter(new URLSearchParams('search=old'), 'q', '').has('search'),
    false,
  );
});

test('marker selection navigates pages and preserves independently expanded cards', () => {
  const selected = selectMapEvent(new URLSearchParams('country=NPL&expanded=first'), {
    id: 'other',
    page: 3,
  });
  assert.equal(selected.get('selected'), 'other');
  assert.equal(selected.get('page'), '3');
  assert.equal(selected.get('country'), 'NPL');
  assert.deepEqual(selected.getAll('expanded'), ['first', 'other']);
  const repeated = selectMapEvent(selected, { id: 'other', page: 1 });
  assert.equal(repeated.has('page'), false);
  assert.deepEqual(repeated.getAll('expanded'), ['first', 'other']);
});

test('marker sizes increase with count within a bounded clickable range', () => {
  assert.ok(markerRadius(1) >= 8);
  assert.ok(markerRadius(5) > markerRadius(1));
  assert.ok(markerRadius(100000) <= 22);
  assert.ok(Number.isFinite(markerRadius(-1)));
});

test('selected event cards are focusable and visibly identified', () => {
  const html = renderToStaticMarkup(
    React.createElement(EventCard, { event, open: true, selected: true, onOpenChange: () => {} }),
  );
  assert.match(html, /tabindex="-1"/);
  assert.match(html, /data-selected="true"/);
});
