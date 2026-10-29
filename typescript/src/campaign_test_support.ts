/** Private test instrumentation; deliberately absent from every package export.
 * A fixed shared snapshot records actual coordinator admission points, with no
 * application callback, channel override, or additional protocol message.
 */
const probes = new WeakMap<object, SharedArrayBuffer>();
export const TRANSPORT_PROBE_FIELDS = Object.freeze({
    version: 0, started: 1, completed: 2, sends: 3, accepted: 4,
    received: 5, queued: 6, blocked: 7, delivering: 8, workers: 9, denied: 10,
});
export const TRANSPORT_PROBE_WORDS = 11;
export function attachCampaignTransportProbe(descriptor: object, probe: BigInt64Array): void {
    if (!(probe.buffer instanceof SharedArrayBuffer) || probe.byteOffset !== 0 || probe.length !== TRANSPORT_PROBE_WORDS) throw new Error('invalid private campaign transport probe');
    probes.set(descriptor, probe.buffer);
}
export function campaignTransportProbe(descriptor: object): SharedArrayBuffer | undefined { return probes.get(descriptor); }
