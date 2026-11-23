import { useEffect, useRef, useState } from 'react';
import type { Map as MapLibreMap, GeoJSONSource } from 'maplibre-gl';
import type { FeatureCollection, Geometry } from 'geojson';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import { Button, Text } from '@radix-ui/themes';
import { getJson } from '../lib/api';
import type { RunArea } from '../lib/run';
import 'maplibre-gl/dist/maplibre-gl.css';

/** Map preview and pointer selection; the adjacent list provides complete keyboard access. */
interface AreaMapProps {
  country: string;
  level: number;
  areas: RunArea[];
  selected: number[];
  onToggle: (id: number) => void;
}

/**
 * Preview simplified area boundaries using Explore's MapLibre basemap and theme colors.
 * Selection updates only the source, while country/level changes refit the camera. Map or tile
 * failures leave the separate searchable list usable. Cleanup releases WebGL and resize resources.
 *
 * @param props - Country, level, complete area choices, and shared selection.
 * @param props.country - Selected ISO3 code.
 * @param props.level - Boundary level to preview.
 * @param props.areas - Complete names and bounds for the current request.
 * @param props.selected - Selected area identifiers.
 * @param props.onToggle - Changes selection through the shared list state.
 * @returns An optional interactive boundary preview with a recoverable loading failure.
 */
export function AreaMap({ country, level, areas, selected, onToggle }: AreaMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const toggle = useRef(onToggle);
  const [readyAttempt, setReadyAttempt] = useState(-1);
  const [mapError, setMapError] = useState('');
  const [boundaryError, setBoundaryError] = useState('');
  const error = mapError || boundaryError;
  const [retry, setRetry] = useState(0);
  const [geometry, setGeometry] = useState<{
    key: string;
    data: FeatureCollection<Geometry>;
  } | null>(null);
  const key = `${country}:${level}`;
  const ready = readyAttempt === retry;

  useEffect(() => {
    toggle.current = onToggle;
  }, [onToggle]);

  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | undefined;

    void import('maplibre-gl')
      .then((lib) => {
        if (disposed || !container.current) return;

        lib.setWorkerUrl(mapWorkerUrl);

        const instance = new lib.Map({
          container: container.current,
          style: 'https://tiles.openfreemap.org/styles/bright',
          center: [20, 15],
          zoom: 1.2,
          attributionControl: { compact: true },
        });

        map.current = instance;
        instance.scrollZoom.disable();
        instance.addControl(new lib.NavigationControl({ showCompass: false }), 'top-right');

        instance.on('load', () => {
          if (disposed) return;

          instance.addSource('areas', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
          });

          instance.addLayer({
            id: 'area-fill',
            type: 'fill',
            source: 'areas',
            paint: {
              'fill-color': ['case', ['get', 'selected'], '#b8481d', '#64748b'],
              'fill-opacity': ['case', ['get', 'selected'], 0.35, 0.08],
            },
          });

          instance.addLayer({
            id: 'area-outline',
            type: 'line',
            source: 'areas',
            paint: {
              'line-color': ['case', ['get', 'selected'], '#8e3b20', '#64748b'],
              'line-width': ['case', ['get', 'selected'], 2.5, 1],
            },
          });

          setMapError('');
          setReadyAttempt(retry);
        });

        instance.on('click', 'area-fill', (event) => {
          const id = Number(event.features?.[0]?.id);

          if (Number.isFinite(id)) toggle.current(id);
        });

        const tooltip = new lib.Popup({ closeButton: false, closeOnClick: false });

        instance.on('mousemove', 'area-fill', (event) => {
          const name = String(event.features?.[0]?.properties?.name || '');

          if (name) tooltip.setLngLat(event.lngLat).setText(name).addTo(instance);

          instance.getCanvas().style.cursor = 'pointer';
        });

        instance.on('mouseleave', 'area-fill', () => {
          tooltip.remove();
          instance.getCanvas().style.cursor = '';
        });

        instance.on('error', () => {
          if (!disposed) setMapError('Map preview unavailable. Use the area controls to continue.');
        });

        observer = new ResizeObserver(() => instance.resize());
        observer.observe(container.current);
      })
      .catch(() => {
        if (!disposed) setMapError('Map preview unavailable. Use the area controls to continue.');
      });

    return () => {
      disposed = true;
      observer?.disconnect();
      map.current?.remove();
      map.current = null;
    };
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();

    getJson<FeatureCollection<Geometry>>(
      `/api/areas/geojson/?country=${encodeURIComponent(country)}&level=${level}`,
      controller.signal,
    ).then(
      (data) => {
        if (!controller.signal.aborted) {
          setGeometry({ key, data });
          setBoundaryError('');
        }
      },
      () => {
        if (!controller.signal.aborted)
          setBoundaryError(
            'Boundaries couldn’t load. Use the area controls, or retry the preview.',
          );
      },
    );

    return () => controller.abort();
  }, [country, level, key, retry]);

  useEffect(() => {
    if (!ready || !map.current) return;

    const ids = new Set(selected);
    const data =
      geometry?.key === key ? geometry.data : { type: 'FeatureCollection' as const, features: [] };

    (map.current.getSource('areas') as GeoJSONSource).setData({
      ...data,
      features: data.features.map((feature) => ({
        ...feature,
        properties: { ...feature.properties, selected: ids.has(Number(feature.id)) },
      })),
    });
  }, [geometry, key, selected, ready]);

  useEffect(() => {
    if (!ready || !map.current || !areas.length) return;

    const boxes = areas.flatMap((area) => (area.bbox ? [area.bbox] : []));

    if (!boxes.length) return;

    map.current.fitBounds(
      [
        [Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1]))],
        [Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))],
      ],
      {
        padding: 40,
        duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 500,
        maxZoom: 9,
      },
    );
  }, [areas, ready]);

  return (
    <div className='area-map-wrap'>
      <div className='area-map' ref={container} role='region' aria-label='Area map preview' />
      {error ? (
        <div className='area-map-message'>
          <Text size='2'>{error}</Text>
          <Button
            type='button'
            size='1'
            variant='soft'
            onClick={() => setRetry((value) => value + 1)}
          >
            Retry preview
          </Button>
        </div>
      ) : null}
    </div>
  );
}
