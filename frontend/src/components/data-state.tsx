import { Button, Callout } from '@radix-ui/themes';

/**
 * Show indeterminate progress while a screen waits for its Django API response.
 *
 * @returns A native progress indicator with an accessible label.
 */
export function Loading() {
  return <progress className='loading' aria-label='Loading analyses' />;
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
