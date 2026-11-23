import { useSearchParams } from 'react-router';
import { Button, Card, Heading, Text, TextField } from '@radix-ui/themes';
import type { NotebookTemplate, Paginated } from '../lib/api';
import { useApi } from '../lib/use-api';
import { Loading, LoadError } from '../components/data-state';

/**
 * List available notebook templates and provide direct access to run configuration and published-analysis search.
 * Run configuration uses the public React workspace; Django continues to validate and execute runs.
 *
 * @returns Template cards, request states, and a search form backed by Django browse.
 */
export function Build() {
  const [params] = useSearchParams();
  const country = params.get('country');
  const countryQuery = country ? `?country=${encodeURIComponent(country)}` : '';
  const result = useApi<Paginated<NotebookTemplate>>('/api/templates/');

  return (
    <>
      <div className='page-heading'>
        <Heading size='8'>Start with a notebook</Heading>
      </div>
      {result.status === 'loading' ? (
        <Loading label='Loading templates' />
      ) : result.status === 'error' ? (
        <LoadError message={result.message} />
      ) : (
        <>
          {result.data.results.length === 0 ? (
            <Card className='empty-state'>
              <Text>No templates are available yet.</Text>
            </Card>
          ) : (
            <div className='card-grid build-template-grid'>
              {result.data.results.map((template) => (
                <Card key={template.slug} className='template-card'>
                  <Heading as='h2' size='5'>
                    {template.title}
                  </Heading>
                  <Text as='p' color='gray'>
                    {template.summary}
                  </Text>
                  <div className='template-actions'>
                    <Button asChild>
                      <a
                        href={`/build/${template.slug}/run${countryQuery}`}
                        aria-label={`Configure analysis: ${template.title}`}
                      >
                        Configure analysis
                      </a>
                    </Button>
                    <a
                      href={`/templates/${template.slug}/${countryQuery}`}
                      aria-label={`View details: ${template.title}`}
                    >
                      View details
                    </a>
                  </div>
                </Card>
              ))}
            </div>
          )}
          {result.data.next ? (
            <Button asChild variant='soft'>
              <a href='/templates/'>Browse all templates</a>
            </Button>
          ) : null}
        </>
      )}
      <section className='existing-analysis'>
        <Heading as='h2' size='5'>
          Start from an existing analysis
        </Heading>
        <form action='/' method='get' className='search-form'>
          <label htmlFor='analysis-search'>Search analyses</label>
          <div className='search-row'>
            <TextField.Root
              id='analysis-search'
              name='q'
              type='search'
              placeholder='Country, title, or disaster ID'
              size='3'
            />
            <Button type='submit' size='3'>
              Search
            </Button>
          </div>
        </form>
      </section>
    </>
  );
}
