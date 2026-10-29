// npm run browser, then open http://127.0.0.1:8080. The installed candidate must
// already contain its built public entry point; this server never builds it.
import { createServer } from 'node:http';
import { readFile, realpath } from 'node:fs/promises';
import { dirname, extname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const exampleRoot = await realpath(dirname(fileURLToPath(import.meta.url)));
const publicEntry = await realpath(fileURLToPath(import.meta.resolve('@jagalite/statelessness')));
const packageRoot = dirname(publicEntry);
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.json': 'application/json' };

createServer(async (request, response) => {
  // These headers enable SharedArrayBuffer where the browser supports it.
  response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
    const isPackage = pathname.startsWith('/package/');
    const root = isPackage ? packageRoot : exampleRoot;
    const path = pathname === '/' ? 'browser.html'
      : pathname.slice(isPackage ? '/package/'.length : 1);
    const file = await realpath(pathname === '/package/index.js' ? publicEntry : resolve(root, path));
    if (relative(root, file).split(/[\\/]/).includes('..')) throw new Error('outside static root');
    const content = await readFile(file);
    response.setHeader('Content-Type', `${mime[extname(file)] ?? 'application/octet-stream'}; charset=utf-8`);
    response.end(content);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain' });
    response.end('Not found. Install a built candidate package before opening this example.');
  }
}).listen(8080, '127.0.0.1', () => console.log('Open http://127.0.0.1:8080'));
