import type { RunConfig } from './run';

/** Session identity and same-origin URLs provided by the Django HTML shell. */
export interface FrontendConfig {
  run: RunConfig | null;
  siteName: string;
  user: { username: string; isStaff: boolean } | null;
  csrfToken: string;
  loginUrl: string;
  logoutUrl: string;
  adminUrl: string;
}

const element = document.getElementById('frontend-config');

if (!element?.textContent) throw new Error('Frontend configuration is missing.');

export const config: FrontendConfig = JSON.parse(element.textContent);
