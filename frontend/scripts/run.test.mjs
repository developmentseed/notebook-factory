import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({
  server: { middlewareMode: true, ws: false },
  appType: 'custom',
});
after(() => server.close());
const { RunEventPicker, RunOptions } = await server.ssrLoadModule('/src/components/run-fields.tsx');

test('submits the stored event reference alongside the initial picker selection', () => {
  const html = renderToStaticMarkup(
    React.createElement(RunEventPicker, {
      html: '<input name="stac_item_id" value="event-1">',
      stacItem: '42',
    }),
  );
  assert.match(html, /type="hidden" name="stac_item" value="42"/);
  assert.match(html, /name="stac_item_id" value="event-1"/);
  assert.doesNotMatch(html, /disabled=""/);
});

test('does not invent a stored reference for a new event selection', () => {
  const html = renderToStaticMarkup(
    React.createElement(RunEventPicker, {
      html: '<select name="stac_catalog"></select>',
      stacItem: '',
    }),
  );
  assert.doesNotMatch(html, /name="stac_item"/);
});

test('closed optional controls remain mounted for native form submission', () => {
  const html = renderToStaticMarkup(
    React.createElement(RunOptions, {
      html: '<input name="event_refs" value="go:123">',
      initialOpen: false,
    }),
  );
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /name="event_refs" value="go:123"/);
  assert.doesNotMatch(html, /disabled=""/);
});
