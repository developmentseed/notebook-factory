import { Button, Heading, Text } from '@radix-ui/themes';
import { useEffect } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router';
import { Layout } from './components/layout';
import { Build } from './pages/build';
import { Explore } from './pages/explore';
import { config } from './lib/config';

/**
 * Define the public React routes and keep the browser title aligned with navigation.
 * Django serves this shell only inside the Explore and Build URL namespaces.
 *
 * @returns The shared layout, public screens, or a scoped not-found screen.
 */
export function App() {
  const { pathname } = useLocation();
  useEffect(() => {
    const title = /^\/explore\/?$/.test(pathname)
      ? 'Explore'
      : /^\/build\/?$/.test(pathname)
        ? 'Build'
        : 'Page not found';
    document.title = `${title} | ${config.siteName}`;
  }, [pathname]);

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path='/explore' element={<Explore />} />
        <Route path='/build' element={<Build />} />
        <Route
          path='*'
          element={
            <div className='empty-state'>
              <Heading>Page not found</Heading>
              <Text as='p'>This page is not available.</Text>
              <Button asChild>
                <Link to='/explore'>Back to Explore</Link>
              </Button>
            </div>
          }
        />
      </Route>
    </Routes>
  );
}
