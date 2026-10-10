import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, readdir, copyFile, rm } from 'node:fs/promises';
import { dirname, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(root, 'sona_code/web/static/vendor/monaco');
const expectedVersion = '0.57.0';
const required = ['monaco.js', 'monaco.css', 'editor.worker.js', 'typescript.worker.js',
  'json.worker.js', 'html.worker.js', 'css.worker.js', 'LICENSE.txt', 'ThirdPartyNotices.txt'];
const sha256 = data => createHash('sha256').update(data).digest('hex');

async function verify() {
  const manifest = JSON.parse(await readFile(resolve(output, 'manifest.json'), 'utf8'));
  if (manifest.version !== expectedVersion || required.some(name => !manifest.files[name])) {
    throw new Error('Missing or unexpected Monaco resources; run npm run editor:build online first.');
  }
  for (const [name, hash] of Object.entries(manifest.files)) {
    if (name.includes('/') || name.includes('\\') || name === '..') throw new Error('Invalid asset path');
    if (sha256(await readFile(resolve(output, name))) !== hash) throw new Error(`Corrupt Monaco asset: ${name}`);
  }
  console.log(`Verified offline Monaco ${manifest.version}: ${Object.keys(manifest.files).length} local assets`);
}

if (process.argv.includes('--check')) {
  await verify();
} else {
  const { build } = await import('esbuild');
  const source = resolve(root, 'node_modules/monaco-editor');
  const metadata = JSON.parse(await readFile(resolve(source, 'package.json'), 'utf8'));
  if (metadata.version !== expectedVersion) throw new Error('Monaco version differs from the pinned build');
  await mkdir(output, { recursive: true });
  // This fixed output path is the only directory cleaned by this build.
  for (const name of await readdir(output)) await rm(resolve(output, name), { force: true });
  const entryPoints = { monaco: resolve(root, 'scripts/monaco-entry.mjs') };
  for (const language of ['editor', 'typescript', 'json', 'html', 'css']) {
    const worker = language === 'editor' ? 'editor/editor.worker.js' : `languages/features/${language}/${language === 'typescript' ? 'ts' : language}.worker.js`;
    entryPoints[`${language}.worker`] = resolve(source, 'esm/vs', worker);
  }
  await build({ entryPoints, outdir: output, bundle: true, splitting: true, format: 'esm',
    platform: 'browser', target: 'es2020', minify: true, sourcemap: false,
    loader: { '.ttf': 'file' }, legalComments: 'eof', chunkNames: 'chunk-[hash]', assetNames: 'asset-[hash]' });
  await copyFile(resolve(source, 'LICENSE'), resolve(output, 'LICENSE.txt'));
  await copyFile(resolve(source, 'ThirdPartyNotices.txt'), resolve(output, 'ThirdPartyNotices.txt'));
  const files = {};
  let bytes = 0;
  for (const name of (await readdir(output)).sort()) {
    const data = await readFile(resolve(output, name));
    files[name] = sha256(data);
    bytes += data.length;
  }
  await writeFile(resolve(output, 'manifest.json'), JSON.stringify({ version: expectedVersion,
    build: 'esbuild-0.28.2-es2020-esm', bytes, files }, null, 2) + '\n');
  console.log(`Built Monaco: ${(bytes / 1024 / 1024).toFixed(2)} MiB in ${relative(root, output)}`);
  await verify();
}
