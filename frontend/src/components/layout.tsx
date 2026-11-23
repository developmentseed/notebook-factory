import { Button, DropdownMenu } from '@radix-ui/themes';
import { useEffect, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { config } from '../lib/config';

/**
 * Render the reduced public navigation around the active React route.
 * Django owns authentication: account links leave React, and logout posts its CSRF token.
 * Client navigation moves focus to the new page content for keyboard users.
 *
 * @returns The public header and an outlet for Explore or Build.
 */
export function Layout() {
  const { pathname, search } = useLocation();
  const country = new URLSearchParams(search).get('country');
  const buildUrl = country ? `/build?country=${encodeURIComponent(country)}` : '/build';
  const main = useRef<HTMLElement>(null);
  const previousPath = useRef(pathname);

  useEffect(() => {
    if (previousPath.current !== pathname) main.current?.focus();

    previousPath.current = pathname;
  }, [pathname]);

  return (
    <>
      <a className='skip-link' href='#main-content'>
        Skip to content
      </a>
      <header className='app-header'>
        <NavLink className='brand' to='/explore'>
          {config.siteName}
        </NavLink>
        <nav aria-label='Primary'>
          <NavLink to='/explore'>Explore</NavLink>
          <NavLink to={buildUrl}>Build</NavLink>
          <a href='/help/'>About</a>
        </nav>
        <div className='account'>
          {config.user ? (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger>
                <Button variant='soft' color='gray'>
                  {config.user.username}
                  <DropdownMenu.TriggerIcon />
                </Button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Content align='end'>
                {config.user.isStaff ? (
                  <DropdownMenu.Item asChild>
                    <a href={config.adminUrl}>Django admin</a>
                  </DropdownMenu.Item>
                ) : null}
                <DropdownMenu.Item asChild>
                  <a href='/templates/'>Manage templates</a>
                </DropdownMenu.Item>
                <DropdownMenu.Item asChild>
                  <a href='/'>All analyses</a>
                </DropdownMenu.Item>
                <DropdownMenu.Separator />
                <DropdownMenu.Item
                  onSelect={() => {
                    document.querySelector<HTMLFormElement>('#logout-form')?.requestSubmit();
                  }}
                >
                  Log out
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Root>
          ) : (
            <Button asChild color='gray' highContrast>
              <a href={`${config.loginUrl}?next=${encodeURIComponent(pathname)}`}>Log in</a>
            </Button>
          )}
        </div>
        {config.user ? (
          <form id='logout-form' method='post' action={config.logoutUrl} hidden>
            <input type='hidden' name='csrfmiddlewaretoken' value={config.csrfToken} />
          </form>
        ) : null}
      </header>
      <main
        id='main-content'
        tabIndex={-1}
        ref={main}
        className={/^\/explore\/?$/.test(pathname) ? 'page explore-page' : 'page'}
      >
        <Outlet />
      </main>
    </>
  );
}
