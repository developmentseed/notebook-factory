import { Badge, Button, Card, Heading, Text, TextField } from '@radix-ui/themes';
import type { NotebookTemplate, Paginated } from '../lib/api';
import { useApi } from '../lib/use-api';
import { Loading, LoadError } from '../components/data-state';

/**
 * List available notebook templates and provide published-analysis search.
 * Template details and run configuration continue through Django's existing pages.
 *
 * @returns Template cards, request states, and a search form backed by Django browse.
 */
export function Build() {
  const result = useApi<Paginated<NotebookTemplate>>('/api/templates/');

  return (
    <>
      <div className='page-heading'>
        <Text className='eyebrow'>Build an analysis</Text>
        <Heading size='8'>Start with a notebook</Heading>
        <Text color='gray' size='3'>
          Choose a template, select your inputs, and run an analysis.
        </Text>
      </div>
      <Heading as='h2' size='5' className='section-heading'>
        Start from a template
      </Heading>
      {result.status === 'loading' ? (
        <Loading />
      ) : result.status === 'error' ? (
        <LoadError message={result.message} />
      ) : (
        <>
          {result.data.results.length === 0 ? (
            <Card className='empty-state'>
              <Text>No templates are available yet.</Text>
            </Card>
          ) : (
            <div className='card-grid'>
              {result.data.results.map((template) => (
                <Card key={template.slug} className='template-card'>
                  <div className='template-meta'>
                    {template.requires_area ? <Badge variant='soft'>Admin area</Badge> : null}
                    {template.requires_stac_item ? (
                      <Badge color='blue' variant='soft'>
                        Event input
                      </Badge>
                    ) : null}
                  </div>
                  <Heading as='h3' size='5'>
                    {template.title}
                  </Heading>
                  <Text as='p' color='gray'>
                    {template.summary}
                  </Text>
                  <Button asChild variant='soft'>
                    <a
                      href={`/templates/${template.slug}/`}
                      aria-label={`View template: ${template.title}`}
                    >
                      View template
                    </a>
                  </Button>
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
        <Text as='p' color='gray'>
          Search published analyses to find a starting point.
        </Text>
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
