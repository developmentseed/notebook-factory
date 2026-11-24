import { Button, Callout, Spinner, VisuallyHidden } from '@radix-ui/themes';

/**
 * Center a small themed spinner while a screen waits for its Django API response.
 *
 * @param props - Optional loading context for the current screen.
 * @param props.label - Accessible description of the pending content.
 * @param props.page - Center within the page content when loading an entire route.
 * @returns A spinner with a screen-reader-only loading label.
 */
export function Loading({
  label = 'Loading analyses',
  page = false,
}: { label?: string; page?: boolean } = {}) {
  return (
    <div className={page ? 'loading loading-page' : 'loading'} role='status'>
      <Spinner size='2' aria-hidden='true' />
      <VisuallyHidden>{label}</VisuallyHidden>
    </div>
  );
}

/**
 * Announce a failed read request to assistive technology and offer a page reload to retry.
 *
 * @param props - Error display options.
 * @param props.message - Human-readable description of the failed request.
 * @returns A Radix error callout with a retry action.
 */
export function LoadError({ message }: { message: string }) {
  return (
    <Callout.Root color='red' role='alert'>
      <Callout.Text>{message}</Callout.Text>
      <Button variant='soft' color='red' onClick={() => window.location.reload()}>
        Try again
      </Button>
    </Callout.Root>
  );
}
