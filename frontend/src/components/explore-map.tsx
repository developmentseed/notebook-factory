import { Button, Text } from '@radix-ui/themes';
import type { MapLibreOverlay } from '@deck.gl/maplibre';
import { ScatterplotLayer } from '@deck.gl/layers';
import type { Map as MapLibreMap } from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { MapEvent } from '../lib/api';
import { markerRadius } from '../lib/explore';
import 'maplibre-gl/dist/maplibre-gl.css';

/** OpenStreetMap geography with warm land, blue water, and readable place labels. */
const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/bright';

/** An event whose validated stored coordinates are available for plotting. */
interface LocatedEvent extends MapEvent {
  position: [number, number];
}

/** Matching marker data and the route-owned selection callback. */
interface ExploreMapProps {
  events: MapEvent[];
  selected: string | null;
  hovered?: string | null;
  loading: boolean;
  onSelect: (event: MapEvent) => void;
}

/**
 * Render MapLibre's world basemap with a deck.gl event layer and count legend.
 * The map survives filter changes; data updates only replace the deck layer. Cleanup releases
 * the observer, map, worker resources and overlay when the Explore route unmounts.
 *
 * @param props - Matching events and selection state from the shared Explore URL.
 * @param props.events - Every matching event, across all sidebar pages.
 * @param props.selected - Event whose circle and sidebar card should be emphasized.
 * @param props.hovered - Sidebar event temporarily emphasized without changing URL selection.
 * @param props.loading - Whether a new filter result is being requested.
 * @param props.onSelect - Opens the matching sidebar card when a marker is clicked.
 * @returns An interactive world map, zoom-to-results control and accessible legend.
 */
export function ExploreMap({
  events,
  selected,
  hovered = null,
  loading,
  onSelect,
}: ExploreMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const overlay = useRef<MapLibreOverlay | null>(null);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const located = useMemo(
    () =>
      events
        .filter((event): event is LocatedEvent => event.position !== null)
        .sort(
          (left, right) =>
            Number(left.id === selected || left.id === hovered) -
            Number(right.id === selected || right.id === hovered),
        ),
    [events, selected, hovered],
  );
  const layers = useMemo(
    () => [
      new ScatterplotLayer<LocatedEvent>({
        id: 'explore-events',
        data: located,
        pickable: true,
        stroked: true,
        radiusUnits: 'pixels',
        lineWidthUnits: 'pixels',
        /**
         * Use the validated longitude/latitude pair from the shared event response.
         *
         * @param event - Located event supplied by Django.
         * @returns Geographic marker position.
         */
        getPosition: (event) => event.position,
        /**
         * Size circles by analysis count, slightly enlarging the sidebar's hovered event.
         *
         * @param event - Event with its filtered analysis count.
         * @returns Screen-space radius in pixels.
         */
        getRadius: (event) => markerRadius(event.analysis_count) + (event.id === hovered ? 2 : 0),
        /**
         * Darken the picked event so its map identity matches the selected card.
         *
         * @param event - Event being drawn.
         * @returns Marker fill as RGBA channels.
         */
        getFillColor: (event) =>
          event.id === selected || event.id === hovered ? [119, 43, 19, 255] : [184, 72, 29, 235],
        /**
         * Keep a contrasting outline visible against country boundaries.
         *
         * @param event - Event being drawn.
         * @returns Marker outline as RGBA channels.
         */
        getLineColor: (event) =>
          event.id === selected || event.id === hovered
            ? [255, 255, 255, 255]
            : [255, 255, 255, 210],
        /**
         * Reinforce selection without relying on fill color alone.
         *
         * @param event - Event being drawn.
         * @returns Outline width in CSS pixels.
         */
        getLineWidth: (event) => (event.id === selected || event.id === hovered ? 3 : 1),
        updateTriggers: {
          getRadius: [hovered],
          getFillColor: [selected, hovered],
          getLineColor: [selected, hovered],
          getLineWidth: [selected, hovered],
        },
        /**
         * Forward a picked event to the route for expansion and page navigation.
         *
         * @param info - deck.gl picking result; empty map clicks have no object.
         */
        onClick: (info) => {
          if (info.object) onSelect(info.object);
        },
      }),
    ],
    [located, selected, hovered, onSelect],
  );

  const currentLayers = useRef(layers);

  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | undefined;

    void Promise.all([import('maplibre-gl'), import('@deck.gl/maplibre')])
      .then(([maplibre, deckModule]) => {
        if (disposed || !container.current) return;

        maplibre.setWorkerUrl(mapWorkerUrl);

        const instance = new maplibre.Map({
          container: container.current,
          style: BASEMAP_STYLE,
          center: [10, 20],
          zoom: 0.65,
          minZoom: 0,
          maxZoom: 12,
          renderWorldCopies: false,
          dragRotate: false,
          attributionControl: { compact: true },
        });

        map.current = instance;

        const deck = new deckModule.MapLibreOverlay({
          interleaved: true,
          layers: currentLayers.current,
          /**
           * Provide plain-text event details without injecting HTML into map overlays.
           *
           * @param info - Current hovered object from deck.gl.
           * @returns Event title and matching count, or no tooltip for empty space.
           */
          getTooltip: (info) =>
            info.object
              ? `${info.object.title}\n${info.object.analysis_count} ${info.object.analysis_count === 1 ? 'analysis' : 'analyses'}`
              : null,
          /** Report renderer failures while keeping the independently loaded event list usable. */
          onError: () => {
            setError(
              'Event markers could not be drawn. All results are available in the event list.',
            );
          },
        });

        overlay.current = deck;

        instance.once('load', () => {
          if (disposed) return;

          instance.addControl(deck);
          setReady(true);
        });

        instance.on('error', () =>
          setError('The basemap could not load. You can still browse the event list.'),
        );

        instance.addControl(new maplibre.NavigationControl({ showCompass: false }), 'top-right');

        instance
          .getCanvas()
          .setAttribute(
            'aria-label',
            'World map of events with published analyses. Use the event list for keyboard access to results.',
          );

        observer = new ResizeObserver(() => instance.resize());
        observer.observe(container.current);
      })
      .catch(() => {
        if (!disposed)
          setError(
            'The map is unavailable in this browser. All results are available in the event list.',
          );
      });

    return () => {
      disposed = true;
      observer?.disconnect();
      map.current?.remove();
      map.current = null;
      overlay.current = null;
    };
  }, []);

  useEffect(() => {
    currentLayers.current = layers;
    overlay.current?.setProps({ layers });
  }, [layers]);

  /** Zoom to the matching event locations without changing their filters or selected card. */
  function fitEvents() {
    if (!map.current || !located.length) return;

    const longitudes = located.map((event) => event.position[0]);
    const latitudes = located.map((event) => event.position[1]);

    map.current.fitBounds(
      [
        [Math.min(...longitudes), Math.min(...latitudes)],
        [Math.max(...longitudes), Math.max(...latitudes)],
      ],
      {
        padding: 64,
        maxZoom: 7,
        duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 500,
      },
    );
  }

  const unlocated = events.length - located.length;

  return (
    <div className='explore-map' aria-label='Event map' aria-busy={loading || (!ready && !error)}>
      <div ref={container} className='explore-map-canvas' />
      <Button
        className='map-fit-events'
        color='gray'
        variant='surface'
        size='2'
        aria-label='Zoom to events'
        title='Zoom to events'
        onClick={fitEvents}
        disabled={!ready || !located.length}
      >
        <svg
          width='16'
          height='16'
          viewBox='0 0 24 24'
          fill='none'
          stroke='currentColor'
          strokeWidth='1.75'
          aria-hidden='true'
        >
          <path d='M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5' />
        </svg>
        <span>Zoom to events</span>
      </Button>
      {!ready && !error ? (
        <Text as='p' role='status' className='map-loading' size='2'>
          Loading map…
        </Text>
      ) : null}
      {error ? (
        <Text as='p' role='status' className='map-notice' size='2'>
          {error}
        </Text>
      ) : null}
      <section className='map-legend' aria-label='Map legend'>
        <Text as='p' size='2' weight='medium'>
          Analyses per event
        </Text>
        <div className='map-legend-sizes' aria-hidden='true'>
          <span>
            <i className='legend-dot legend-dot-one' />1
          </span>
          <span>
            <i className='legend-dot legend-dot-five' />5
          </span>
          <span>
            <i className='legend-dot legend-dot-ten' />
            10+
          </span>
        </div>
        <Text as='p' size='1' color='gray' className='explore-sr-only'>
          Circle size shows analysis count. Select a circle to open its event.
        </Text>
        {unlocated ? (
          <Text as='p' size='1' color='gray'>
            {unlocated} {unlocated === 1 ? 'event has' : 'events have'} no map location.
          </Text>
        ) : null}
      </section>
    </div>
  );
}
