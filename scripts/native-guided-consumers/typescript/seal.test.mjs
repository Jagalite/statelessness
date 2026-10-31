// Synthetic admission tests only; no corpus expectations or candidate engine run.
import test from 'node:test';
import assert from 'node:assert/strict';
import { CORPUS_SEAL_PATH, validatePublishedSeal } from './seal.mjs';

const authority = 'a4cc11eea8db62115b452eee3c6610c04fa06b96';
const hash = 'a'.repeat(64);
const seal = () => ({ schema:'stateless-guided-v2-freeze/1', revision:3, source_commit:authority,
  candidate_output_used:false, cases:87, files:{[CORPUS_SEAL_PATH]:hash} });

test('published files-map seal admits the bound corpus without a top-level corpus_sha256', () => {
  const value=seal();
  assert.equal(Object.hasOwn(value,'corpus_sha256'),false);
  assert.doesNotThrow(()=>validatePublishedSeal(value,authority,hash));
});

test('legacy top-level hashes, mismatches, and malformed independent seals are rejected', () => {
  const legacy={...seal(),corpus_sha256:hash};delete legacy.files;
  const inherited={...seal(),files:Object.create({[CORPUS_SEAL_PATH]:hash})};
  for (const value of [legacy,inherited,null,[],{...seal(),schema:'unknown'},{...seal(),source_commit:'other'},
    {...seal(),candidate_output_used:true},{...seal(),cases:0},{...seal(),revision:0},
    {...seal(),files:{[CORPUS_SEAL_PATH]:'b'.repeat(64)}}]) {
    assert.throws(()=>validatePublishedSeal(value,authority,hash),/FREEZE.json does not bind/);
  }
});
