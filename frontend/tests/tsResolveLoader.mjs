// Node's built-in TypeScript stripping keeps import specifiers exactly as the
// bundler sees them, and the source uses extensionless relative imports. This
// resolver maps those to the real file so tests can import product modules
// without a bundler or a new dependency.

import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && !/\.[mc]?[jt]sx?$/.test(specifier)) {
    for (const extension of ['.ts', '.tsx', '.js']) {
      const candidate = new URL(specifier + extension, context.parentURL);
      if (existsSync(fileURLToPath(candidate))) {
        return nextResolve(specifier + extension, context);
      }
    }
  }
  return nextResolve(specifier, context);
}
