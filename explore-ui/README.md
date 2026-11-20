# Explore UI

A React front end for browsing published notebooks: filters, a map and
notebooks grouped by event. It reads the Notebook Factory REST API
(`/api/runs/?status=published`) and is built with Vite, React, TypeScript,
Tailwind and shadcn/ui.

## Run it

```bash
npm install
npm run dev
```

The dev server runs on http://localhost:5173 and proxies `/api` to the staging
API (see `vite.config.ts`), because the API sends no CORS headers. A deployed
build needs the API to allow its origin, or a proxy rule on the host.

## Checks

```bash
npm run lint
npx tsc -b
npm run build
```

## Notes

- Colours for hazards are theme tokens (`--category-*` in `src/index.css`),
  mapped by hazard key in `src/lib/categories.ts`.
- Hazard icons are OCHA Humanitarian Icons (CC0), in `src/assets/ocha-icons`.
