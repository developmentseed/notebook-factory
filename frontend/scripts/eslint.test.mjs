import assert from 'node:assert/strict';
import test from 'node:test';
import { ESLint } from 'eslint';

const eslint = new ESLint();

/**
 * Check a TypeScript snippet with the real frontend configuration without creating a source file.
 *
 * @param source - A snippet that intentionally exercises the JSDoc policy.
 * @returns Missing-documentation messages, excluding unrelated lint diagnostics.
 */
async function missingDocumentation(source) {
  const [result] = await eslint.lintText(source, { filePath: 'src/lib/lint-policy-probe.ts' });
  return result.messages.filter((message) => message.ruleId === 'jsdoc/require-jsdoc');
}

for (const [name, source] of [
  ['function declaration', 'export function title(value: string) { return value.trim(); }'],
  ['named arrow function', 'export const title = (value: string) => value.trim();'],
  ['function expression', 'export const title = function(value: string) { return value.trim(); };'],
]) {
  test(`requires JSDoc for a ${name}`, async () => {
    assert.equal((await missingDocumentation(source)).length, 1);
  });

  test(`accepts JSDoc for a ${name}`, async () => {
    const documented = `/**
 * Remove surrounding whitespace from a title.
 *
 * @param value - The title to normalize.
 * @returns The title without surrounding whitespace.
 */
${source}`;
    assert.equal((await missingDocumentation(documented)).length, 0);
  });
}

test('does not require JSDoc on inline callbacks', async () => {
  const source = 'export const doubled = [1, 2].map((value) => value * 2);';
  assert.equal((await missingDocumentation(source)).length, 0);
});

test('requires spacing around guards, effects and returns', async () => {
  const source = `function preview() {
  const country = 'NPL';
  if (!country) return;
  console.log(country);
  console.log(
    country,
  );
  console.log(
    country,
  );
  return country;
}`;
  const [result] = await eslint.lintText(source, { filePath: 'src/lib/lint-policy-probe.ts' });
  const spacing = result.messages.filter(
    (message) => message.ruleId === '@stylistic/padding-line-between-statements',
  );
  assert.deepEqual(
    spacing.map((message) => message.line),
    [3, 4, 5, 8, 11],
  );
  assert.ok(spacing.every((message) => message.fix));
});

test('keeps related declarations and short calls together', async () => {
  const source = `function preview() {
  const country = 'NPL';
  const level = 0;

  console.log(country);
  console.log(level);

  return country;
}`;
  const [result] = await eslint.lintText(source, { filePath: 'src/lib/lint-policy-probe.ts' });
  assert.equal(
    result.messages.filter(
      (message) => message.ruleId === '@stylistic/padding-line-between-statements',
    ).length,
    0,
  );
});
