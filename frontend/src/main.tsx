import 'vite/modulepreload-polyfill';
import '@radix-ui/themes/styles.css';
import './styles.css';
import { Theme } from '@radix-ui/themes';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { App } from './app';

const root = document.getElementById('react-root');
if (!root) throw new Error('React root is missing.');

createRoot(root).render(
  <StrictMode>
    <Theme accentColor='orange' grayColor='slate' radius='medium' appearance='light'>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Theme>
  </StrictMode>,
);
