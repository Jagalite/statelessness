import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptModel, declaredProperties, ModelError } from '../dist/index.js';

class UntrustedList extends Array {
  map() { return [{id: 'forged', phase: 'state'}]; }
}

test('property declarations validate actual array slots, not an overridden map', () => {
  assert.throws(() => declaredProperties(new UntrustedList(
    {id: 'same', phase: 'state'}, {id: 'same', phase: 'transition'})), ModelError);
  assert.throws(() => declaredProperties(new UntrustedList({id: 'x', phase: 'other'})), ModelError);
  const m = {initialState: () => 0, step: () => ({}), cloneState: x => x,
    cloneInput: x => x, cloneOutput: x => x, checkState: () => []};
  assert.throws(() => adaptModel(m, {properties: new UntrustedList()}), /no properties/);
});

test('property ids and phases are captured once before validation and publication', () => {
  let ids = 0, phases = 0;
  const source = [{get id() { return ++ids === 1 ? 'safe' : 'changed'; },
    get phase() { return ++phases === 1 ? 'state' : 'invalid'; }}];
  const result = declaredProperties(source);
  assert.deepEqual(result, [{id: 'safe', phase: 'state'}]);
  assert.equal(ids, 1); assert.equal(phases, 1);
  assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result[0]));
});
