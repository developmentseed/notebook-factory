# Contributor and agent preferences

## Frontend architecture

- Use React, TypeScript, and React Router for the public Explore and Build routes.
- Keep Django responsible for the backend, session authentication, permissions, and admin portal.
- Use Radix Themes for components and design foundations. Use Radix Primitives when a needed
  interaction is not covered by Themes. Reuse existing components and theme tokens before adding
  custom controls or another UI library.
- Keep frontend modules in `frontend/src/`, with pages, shared components, and API utilities separate.
- Use existing Django APIs where they fit. Keep requests on the Django origin and include its CSRF
  token for writes. Preserve the existing management and notebook viewer routes.

## Documentation and comments

- Always write JSDoc for named JavaScript/TypeScript functions, React components, hooks, and exported
  interfaces/type contracts that you add or materially change. Small inline callbacks can be explained
  by the surrounding function's JSDoc rather than given repetitive individual comments.
- Follow the official [JSDoc patterns](https://jsdoc.app/about-getting-started), including
  [`@param`](https://jsdoc.app/tags-param), [`@returns`](https://jsdoc.app/tags-returns),
  [`@throws`](https://jsdoc.app/tags-throws), and [`@example`](https://jsdoc.app/tags-example) when useful.
- Explain the code in clear terms: its purpose, meaningful inputs and outputs, side effects, ownership,
  or lifecycle constraints. Document the reason for a constraint when it helps the next maintainer.
- Let TypeScript declare types. Do not repeat TypeScript signatures as JSDoc `{type}` annotations.
  Describe parameter meaning and return behavior instead. Do not add empty tags to satisfy a rule.
- Avoid noisy one-line comments that narrate obvious statements, restate names, or describe obsolete
  behavior. Keep implementation explanations in the relevant JSDoc and update them with the code.
- For Python, follow the same clarity principles using Python docstrings rather than JSDoc syntax.

## Checks and formatting

- Use ESLint for TypeScript, React hooks, and JSDoc checks; Prettier owns frontend formatting.
- Run `npm run check --prefix frontend` and `npm run build --prefix frontend` after frontend changes.
- Use Ruff and focused pytest checks when changing the Django integration.
- Lefthook formats and re-stages matching staged frontend files before lint and type checks on commit.
  Do not bypass hooks to make failing checks disappear.
- Do not commit or push unless the user explicitly requests it.

## Writing and GitHub

- Use clear, short descriptions and avoid the middle dot character in authored UI copy and documents.
- Use the GitHub CLI account saved in macOS Keychain:
  `env -u GITHUB_TOKEN -u GH_TOKEN gh ...`. Never print or persist authentication tokens.
