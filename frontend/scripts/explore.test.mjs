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
const { exploreEventsUrl, explorePageUrl, eventCardId, analysisText } =
  await server.ssrLoadModule('/src/lib/explore.ts');
const { EventCard } = await server.ssrLoadModule('/src/components/event-card.tsx');
const { EventSidebar } = await server.ssrLoadModule('/src/components/event-sidebar.tsx');

const event = {
  id: '1:corr:nepal/flood',
  title: 'Flood in Nepal 2026',
  catalog: 'montandon',
  date: '2026-08-25T00:00:00Z',
  purposes: ['Post-disaster impact'],
  analysis_count: 1,
  analyses: [
    {
      id: 'analysis-1',
      title: 'Impact estimate',
      purpose: 'Post-disaster impact',
      created_at: '2026-08-26T00:00:00Z',
      viewer_url: '/runs/analysis-1/view/',
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
