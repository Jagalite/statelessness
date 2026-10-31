import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {RUNTIME_SOURCE_BUILD_ID} from '../dist/index.js';

test('source build identity is deterministic and changes with source inputs', () => {
  assert.match(RUNTIME_SOURCE_BUILD_ID, /^sha256:[0-9a-f]{64}$/);
  const tmp = mkdtempSync(join(tmpdir(), 'stateless-identity-'));
  try {
    for (const path of ['src', 'scripts', 'package.json', 'package-lock.json', 'tsconfig.json'])
      cpSync(new URL(`../${path}`, import.meta.url), join(tmp, path), {recursive: true});
    const run = () => {
      const result = spawnSync(process.execPath, [join(tmp, 'scripts/runtime-identity.mjs')]);
      assert.equal(result.status, 0, result.stderr.toString());
      return readFileSync(join(tmp, 'src/runtime_identity.ts'), 'utf8');
    };
    const original = run();
    assert.match(original, new RegExp(RUNTIME_SOURCE_BUILD_ID));
    assert.equal(run(), original);
    const source = join(tmp, 'src/modeling.ts');
    writeFileSync(source, readFileSync(source, 'utf8') + '\n// identity control\n');
    assert.notEqual(run(), original);
    const changed = run();
    // Windows forbids '?' in file names and needs privileges for symbolic links.
    const windows = process.platform === 'win32';
    writeFileSync(join(tmp, windows ? 'src/special#%.ts' : 'src/special#?%.ts'), 'export {};\n');
    assert.notEqual(run(), changed);
    if (!windows) {
      symlinkSync(source, join(tmp, 'src/linked.ts'));
      const rejected = spawnSync(process.execPath, [join(tmp, 'scripts/runtime-identity.mjs')]);
      assert.notEqual(rejected.status, 0);
      assert.match(rejected.stderr.toString(), /symbolic link in source tree/);
    }
  } finally { rmSync(tmp, {recursive: true, force: true}); }
});

test('reference source identity frames each file so boundary shifts change it', () => {
  const tmp = mkdtempSync(join(tmpdir(), 'stateless-reference-identity-'));
  try {
    const pkg = join(tmp, 'typescript');
    for (const path of ['scripts/reference-identity.mjs', 'src/reference_controller.ts', 'src/reference_host.ts'])
      cpSync(new URL(`../${path}`, import.meta.url), join(pkg, path), {recursive: true});
    cpSync(new URL('../../docs/requirements', import.meta.url), join(tmp, 'docs/requirements'), {recursive: true});
    const run = () => {
      const result = spawnSync(process.execPath, [join(pkg, 'scripts/reference-identity.mjs')]);
      assert.equal(result.status, 0, result.stderr.toString());
      return /REFERENCE_SOURCE_ID = '([^']+)'/.exec(readFileSync(join(pkg, 'src/reference_identity.ts'), 'utf8'))[1];
    };
    const original = run();
    assert.match(original, /^fnv1a64:[0-9a-f]{16}$/);
    assert.equal(run(), original);
    // Move one byte from the end of the controller to the start of the host:
    // the unframed concatenation is unchanged, the framed identity is not.
    const controller = join(pkg, 'src/reference_controller.ts'), host = join(pkg, 'src/reference_host.ts');
    const a = readFileSync(controller), b = readFileSync(host);
    writeFileSync(controller, a.subarray(0, a.length - 1));
    writeFileSync(host, Buffer.concat([a.subarray(a.length - 1), b]));
    assert.notEqual(run(), original);
  } finally { rmSync(tmp, {recursive: true, force: true}); }
});
