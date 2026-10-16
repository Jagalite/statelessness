// Pure value validation shared by the ABI2 transport and portable JSON codec.
export const DEFAULT_TRACE_LIMITS = Object.freeze({maxSteps:100_000,maxBlobBytes:4*1024*1024,maxItems:250_000,maxPayloadBytes:32*1024*1024,maxJSONBytes:64*1024*1024});
export function scalar(value) {
  if (typeof value !== 'string') throw new TypeError('expected string');
  for (let i=0;i<value.length;i++) {
    const c=value.charCodeAt(i);
    if (c>=0xd800 && c<=0xdbff) {const next=value.charCodeAt(++i);if (!(next>=0xdc00&&next<=0xdfff)) throw new TypeError('invalid Unicode scalar');}
    else if (c>=0xdc00&&c<=0xdfff) throw new TypeError('invalid Unicode scalar');
  }
  return value;
}
export function natural(value,name,minimum=0,maximum=Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value)||value<minimum||value>maximum) throw new RangeError(`invalid ${name}`);
  return value;
}
export function validateTraceLimits(limits) {
  for (const key of Object.keys(DEFAULT_TRACE_LIMITS)) natural(limits[key],key);
}
export function check(id,status='passed',details='') {
  scalar(id);scalar(details);
  if (!['passed','failed','skipped'].includes(status)||(status==='passed'&&details!=='')) throw new TypeError('invalid check');
  return {id,status,details};
}
export function disposition(kind='accepted',reason='') {
  scalar(reason);
  if (!['accepted','rejected','ignored'].includes(kind)||(kind==='accepted'&&reason!=='')) throw new TypeError('invalid disposition');
  return {kind,reason};
}
export function metadata(value) {
  scalar(value.name);scalar(value.build);
  for (const key of ['model_version','properties_version','codec_version']) natural(value[key],key,0,0xffffffff);
  return {...value};
}
