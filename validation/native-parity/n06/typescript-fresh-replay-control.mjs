#!/usr/bin/env node
// Source-tree N06 control: a real shrunk witness is replayed in a new process.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { enumerateStates, shrink, recordAudit, writeAuditTrace, readAuditTrace, replay } from '../../../typescript/dist/index.js';
import { AuditedLifecycle, claim, complete, receive, sink } from '../../../typescript/test/fixtures/campaign-lifecycle.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const config = {strategy:'campaign-self-model-fault-control', seed:null, parameters:[['injected_fault','duplicate Deliver']]};
function requireThat(value, message) { if (!value) throw new Error(message); }
function produce(dir) {
  const model = new AuditedLifecycle({duplicateDelivery:true});
  const found = enumerateStates(model,{maxStates:10000,maxTransitions:100000,maxDepth:10});
  requireThat(found.termination === 'failure_found', 'fault not discovered');
  const violations = found.failure.violations.filter(v => v.phase === 'transition' && v.check.id === 'campaign.effects');
  requireThat(violations.length > 0, 'expected transition failure absent');
  const original = {inputs:[sink(999n),claim(),...found.failure.inputs],violations};
  const result = shrink(model,original,{maxAttempts:1000});
  requireThat(result.validated_original && result.termination === 'search_complete', 'shrink incomplete');
  assert.deepEqual(result.minimized.inputs,[claim(),complete(50n),receive(50n)],'unexpected minimized witness');
  requireThat(original.inputs.length > 3, 'original not larger than witness');
  for (const [name,inputs] of [['witness.bin',result.minimized.inputs],['original.bin',original.inputs]]) {
    const trace=recordAudit(model,inputs,config,100);
    requireThat(trace.termination === 'property_failed','record did not fail');
    writeFileSync(join(dir,name),writeAuditTrace(trace),{flag:'wx'});
  }
}
function consume(path) {
  const trace=readAuditTrace(readFileSync(path)), model=new AuditedLifecycle({duplicateDelivery:true});
  requireThat(trace.config.strategy === config.strategy,'unexpected configuration');
  const actual=trace.steps.map(step=>model.decodeInput(step.input));
  assert.deepEqual(actual,[claim(),complete(50n),receive(50n)],'unexpected persisted witness');
  requireThat(trace.steps.at(-1).checks.some(c=>c.id==='campaign.effects' && c.status==='failed'),'wrong failure identity');
  const got=replay(model,trace);
  requireThat(got.outcome==='exact' && got.steps_verified===3 && got.failure_reproduced,'fault not exactly reproduced');
  const healthy=new AuditedLifecycle();
  const strict=replay(healthy,trace);
  requireThat(strict.outcome==='incompatible' && !strict.build_matches,'healthy strict control failed');
  const relaxed=replay(healthy,trace,true);
  requireThat(relaxed.outcome==='diverged' && !relaxed.failure_reproduced,'healthy observation control failed');
  console.log('fresh-replay-verified');
}
function digest(path) { return createHash('sha256').update(readFileSync(path)).digest('hex'); }
function files(dir) { return readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?files(join(dir,e.name)):e.isFile()?[join(dir,e.name)]:[]); }
function exercise(dir) {
  produce(dir);
  const command=[fileURLToPath(import.meta.url),'consume'];
  for (const [prefix,file,positive] of [['replay','witness.bin',true],['unreduced','original.bin',false]]) {
    const run=spawnSync(process.execPath,[...command,join(dir,file)],{encoding:'utf8',timeout:120000,maxBuffer:1024*1024});
    writeFileSync(join(dir,`${prefix}.stdout`),run.stdout??''); writeFileSync(join(dir,`${prefix}.stderr`),run.stderr??'');
    requireThat(!run.error && run.signal===null,`${prefix} process error or signal`);
    if (positive) requireThat(run.status===0 && run.stdout.trim()==='fresh-replay-verified','fresh replay failed');
    else requireThat(run.status!==0 && run.stderr.includes('unexpected persisted witness'),'unreduced negative control failed');
  }
  const sources=[fileURLToPath(import.meta.url),resolve(root,'typescript/test/fixtures/campaign-lifecycle.mjs'),...files(resolve(root,'typescript/src')),...files(resolve(root,'typescript/dist'))].sort();
  const artifacts=Object.fromEntries(readdirSync(dir).sort().map(n=>[n,digest(join(dir,n))]));
  writeFileSync(join(dir,'manifest.json'),JSON.stringify({scope:'source-tree, caller-attested, single host',node:process.version,producer_argv:process.argv,consumer_argv:[process.execPath,...command],sources:Object.fromEntries(sources.map(p=>[relative(root,p),digest(p)])),artifacts},null,2)+'\n',{flag:'wx'});
  console.log('fresh-process fault and unreduced negative control passed');
}
const args=process.argv.slice(2);
if (args[0]==='consume' && args.length===2) consume(args[1]);
else if (args[0]==='--output' && args.length===2) { const dir=resolve(args[1]); mkdirSync(dir,{recursive:false}); exercise(dir); }
else if (args.length===0) { const dir=mkdtempSync(join(tmpdir(),'n06-ts-fresh-')); try { exercise(dir); } finally { rmSync(dir,{recursive:true,force:true}); } }
else throw new Error('usage: typescript-fresh-replay-control.mjs [consume TRACE | --output NEW_DIRECTORY]');
