import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const demo=fileURLToPath(new URL('../bin/demo.mjs',import.meta.url));
const run=(...args)=>spawnSync(process.execPath,[demo,...args],{encoding:'utf8'});
test('demo CLI explores fixed and injected request fixtures',()=>{
 const fixed=run('explore','fixed'),buggy=run('explore','buggy');assert.equal(fixed.status,0,fixed.stderr);assert.equal(buggy.status,0,buggy.stderr);
 assert.equal(JSON.parse(fixed.stdout).result.termination,'graph_exhausted');assert.equal(JSON.parse(buggy.stdout).result.termination,'failure_found');
});
test('demo CLI validates mode, variant and seed',()=>{
 assert.equal(run('--help').status,0);for(const args of [[],['explore','bad'],['explore','fixed','1'],['fuzz','fixed','-1'],['fuzz','fixed','18446744073709551616']])assert.notEqual(run(...args).status,0);
 const result=run('fuzz','buggy','42');assert.equal(result.status,0,result.stderr);assert.equal(JSON.parse(result.stdout).operation,'fuzz');
 assert.equal(run('fuzz','fixed','9'.repeat(10000)).status,1);
 assert.equal(run('fuzz','fixed','0'.repeat(10000)+'42').status,0);
 assert.equal(run('fuzz','fixed','00018446744073709551615').status,0);
});
