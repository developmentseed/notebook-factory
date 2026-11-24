import * as Collapsible from '@radix-ui/react-collapsible';
import { ChevronRightIcon } from '@radix-ui/react-icons';
import { Button, Text } from '@radix-ui/themes';
import { useEffect, useRef, useState } from 'react';

/** Autoescaped HTML rendered only by this application's Django field templates. */
interface DjangoFieldsProps {
  html: string;
}

/** Picker markup and an existing stored event that can be reused until the selection changes. */
interface RunEventPickerProps extends DjangoFieldsProps {
  stacItem: string;
}

/**
 * Reuse Django's schema-driven fields and bound values without reimplementing parameter widgets.
 * This slot accepts trusted same-origin template output, never raw user or catalog HTML.
 *
 * @param props - Server-rendered, autoescaped field markup.
 * @param props.html - HTML produced by the existing parameter templates.
 * @returns Native inputs that submit to Django with the surrounding React form.
 */
export function DjangoFields({ html }: DjangoFieldsProps) {
  return (
    <div
      className='run-fields'
      onInput={(event) => {
        const input = event.target;

        if (
          input instanceof HTMLInputElement &&
          input.type === 'range' &&
          input.nextElementSibling instanceof HTMLOutputElement
        )
          input.nextElementSibling.value = input.value;
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/**
 * Keep the existing catalog/collection/item picker backed by Django's single choice service.
 * Delegated React events replace HTMX on this route. Aborted and superseded requests cannot
 * overwrite newer choices; field focus returns after the server replaces its dependent choices.
 *
 * @param props - Initial autoescaped picker markup from Django.
 * @param props.html - Catalog picker HTML produced by Django.
 * @param props.stacItem - Stored event identifier reused while the initial selection stays unchanged.
 * @returns Catalog and event inputs with request feedback and retry support.
 */
export function RunEventPicker({ html: initialHtml, stacItem }: RunEventPickerProps) {
  const [html, setHtml] = useState(initialHtml);
  const [storedItem, setStoredItem] = useState(stacItem);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const request = useRef<AbortController | null>(null);
  const focusName = useRef('');

  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    if (focusName.current)
      root.current?.querySelector<HTMLElement>(`[name="${focusName.current}"]`)?.focus();
  }, [html]);

  /** Fetch replacement choices using the existing picker endpoint and current native inputs. */
  async function refresh() {
    const picker = root.current?.querySelector<HTMLElement>('#stac-picker');
    const form = picker?.closest('form');
    const url = picker?.getAttribute('hx-get');

    if (!form || !url) return;

    request.current?.abort();

    const controller = new AbortController();

    request.current = controller;

    const params = new URLSearchParams();

    new FormData(form).forEach((value, key) => {
      if (key.startsWith('stac_') && typeof value === 'string') params.append(key, value);
    });

    setLoading(true);
    setError('');

    try {
      const response = await fetch(`${url}?${params}`, {
        signal: controller.signal,
        credentials: 'same-origin',
        headers: { 'HX-Request': 'true' },
      });

      if (!response.ok || response.redirected) throw new Error('Picker unavailable');

      const markup = await response.text();

      if (!controller.signal.aborted) setHtml(markup);
    } catch {
      if (!controller.signal.aborted)
        setError('We couldn’t load event choices. Try again, or keep your current selection.');
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <div
      ref={root}
      className='run-fields run-event-picker'
      aria-busy={loading}
      onChange={(event) => {
        const target = event.target;

        if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) return;

        if (['stac_catalog', 'stac_collection', 'stac_item_id'].includes(target.name))
          setStoredItem('');

        if (!['stac_catalog', 'stac_collection', 'stac_show_all'].includes(target.name)) return;

        focusName.current = target.name;

        if (target.name !== 'stac_show_all') {
          const item = root.current?.querySelector<HTMLInputElement>('[name="stac_item_id"]');
          const search = root.current?.querySelector<HTMLInputElement>('[name="stac_q"]');

          if (item) item.value = '';

          if (search) search.value = '';
        }

        void refresh();
      }}
      onClick={(event) => {
        if (!(event.target instanceof Element)) return;

        const item = event.target.closest<HTMLElement>('[data-stac-item]');

        if (item) {
          const input = root.current?.querySelector<HTMLInputElement>('[name="stac_item_id"]');

          if (input) input.value = item.dataset.stacItem || '';

          setStoredItem('');

          root.current
            ?.querySelectorAll('.stac-items li')
            .forEach((row) => row.classList.remove('selected'));

          item.closest('li')?.classList.add('selected');
        }

        if (event.target.closest('#stac_search')) {
          event.preventDefault();
          focusName.current = 'stac_q';
          void refresh();
        }
      }}
      onKeyDown={(event) => {
        if (
          event.key === 'Enter' &&
          !event.nativeEvent.isComposing &&
          event.target instanceof HTMLInputElement &&
          event.target.name === 'stac_q'
        ) {
          event.preventDefault();
          focusName.current = 'stac_q';
          void refresh();
        }
      }}
    >
      <fieldset disabled={loading} className='run-event-fields'>
        <legend className='run-sr-only'>Event source and item</legend>
        {storedItem ? <input type='hidden' name='stac_item' value={storedItem} /> : null}
        <div dangerouslySetInnerHTML={{ __html: html }} />
      </fieldset>
      {loading ? (
        <Text as='p' size='2' color='gray'>
          Loading event choices…
        </Text>
      ) : null}
      {error ? (
        <div role='alert'>
          <Text as='p' color='red'>
            {error}
          </Text>
          <Button type='button' variant='soft' onClick={() => void refresh()}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Trusted optional-field markup and whether Django returned an error inside it. */
interface RunOptionsProps extends DjangoFieldsProps {
  initialOpen: boolean;
}

/**
 * Reveal optional fields with the same disclosure primitive used by Explore event cards.
 * Keep closed fields mounted so native submission retains their current values; CSS removes
 * them from layout and the keyboard order. Server errors open the section on the next render.
 *
 * @param props - Autoescaped metadata fields and their initial disclosure state.
 * @param props.html - Django's label and notification controls, including bound values.
 * @param props.initialOpen - Open the section when Django reports an error in these fields.
 * @returns A keyboard-operable disclosure with a Radix chevron and persistent form controls.
 */
export function RunOptions({ html, initialOpen }: RunOptionsProps) {
  return (
    <Collapsible.Root className='run-options' defaultOpen={initialOpen}>
      <Collapsible.Trigger type='button' className='run-options-trigger'>
        <ChevronRightIcon aria-hidden='true' className='run-options-chevron' />
        Labels and notifications (optional)
      </Collapsible.Trigger>
      <Collapsible.Content forceMount className='run-options-content'>
        <DjangoFields html={html} />
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
