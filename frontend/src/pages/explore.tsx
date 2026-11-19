import { Badge, Button, Card, Heading, Text } from '@radix-ui/themes';
import type { NotebookRun, Paginated } from '../lib/api';
import { useApi } from '../lib/use-api';
import { Loading, LoadError } from '../components/data-state';

/**
 * Show published analyses from the existing API and link to their Django viewers.
 * Event grouping, shared map filters, and markers will be added in the Explore tickets.
 *
 * @returns Published result cards or the corresponding loading, error, or empty state.
 */
export function Explore() {
  const result = useApi<Paginated<NotebookRun>>('/api/runs/?status=published&ordering=-created_at');

  return (
    <>
      <div className='page-heading'>
        <div>
          <Text className='eyebrow'>Montandon Notebooks</Text>
          <Heading size='8'>Explore analyses</Heading>
        </div>
        <Text color='gray' size='3'>
          Discover published notebook analyses and open their results.
        </Text>
      </div>
      {result.status === 'loading' ? (
        <Loading />
      ) : result.status === 'error' ? (
        <LoadError message={result.message} />
      ) : (
        <>
          <Text as='p' color='gray' className='results-count'>
            {result.data.count} published {result.data.count === 1 ? 'analysis' : 'analyses'}
          </Text>
          {result.data.results.length === 0 ? (
            <Card className='empty-state'>
              <Heading size='4'>No published analyses yet</Heading>
              <Text as='p' color='gray'>
                Choose a template in Build to start an analysis.
              </Text>
            </Card>
          ) : (
            <div className='card-grid'>
              {result.data.results.map((run) => (
                <Card key={run.id} className='analysis-card'>
                  <Badge color='green' variant='soft'>
                    Published
                  </Badge>
                  <Heading as='h2' size='5'>
                    {run.title}
                  </Heading>
                  <Text as='p' color='gray'>
                    {run.summary || run.subtitle}
                  </Text>
                  <div className='card-footer'>
                    <Text size='2' color='gray'>
                      <time dateTime={run.created_at}>
                        {new Date(run.created_at).toLocaleDateString()}
                      </time>
                    </Text>
                    <Button asChild variant='soft'>
                      <a
                        href={`/runs/${run.id}/view/`}
                        aria-label={`Open analysis: ${run.title}, ${new Date(run.created_at).toLocaleString()}`}
                      >
                        Open analysis
                      </a>
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          )}
          {result.data.next ? (
            <Button asChild variant='soft'>
              <a href='/'>Browse all analyses</a>
            </Button>
          ) : null}
        </>
      )}
    </>
  );
}
