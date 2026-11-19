import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const repository = fileURLToPath(new URL('../../', import.meta.url));

if (existsSync(new URL('../../.git', import.meta.url))) {
  const result = spawnSync('lefthook', ['install'], { cwd: repository, stdio: 'inherit' });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}
