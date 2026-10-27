/** Exact Rust binary-v1 arithmetic shared by bounded recording and actual replay evidence. */
import { type Metadata, type Check, type Disposition, ModelError, scalar, natural } from './core.js';
import { type BinaryLimits, type RunConfig, auditRunLayout, resolveBinaryLimits, validateAuditTrace, snapshotAuditTrace } from './binary.js';
export { resolveBinaryLimits, validateAuditTrace, snapshotAuditTrace };
const U32 = 0xffffffff;
const limit = (condition: boolean, label: string) => { if (!condition)
    throw new ModelError(`bounded evidence limit: ${label}`); };
function sum(...values: number[]): number { let n = 0; for (const v of values) {
    n += v;
    limit(Number.isSafeInteger(n), 'byte count overflow');
} return n; }
function stringSize(value: string, l: BinaryLimits): number {
    scalar(value);
    let n = 0;
    for (const c of value) {
        const cp = c.codePointAt(0)!;
        n = sum(n, cp < 128 ? 1 : cp < 2048 ? 2 : cp < 65536 ? 3 : 4);
    }
    limit(n <= Math.min(U32, l.max_string_bytes), 'string bytes');
    return sum(4, n);
}
function batchSize(checks: readonly Check[], l: BinaryLimits): number {
    limit(checks.length <= Math.min(U32, l.max_checks_per_step), 'checks per step');
    let bytes = 4;
    for (const c of checks) {
        bytes = sum(bytes, stringSize(c.id, l), 1);
        if (c.status === 'passed')
            limit(c.details === '', 'passed check reason');
        else if (c.status === 'failed' || c.status === 'skipped')
            bytes = sum(bytes, stringSize(c.details, l));
        else
            limit(false, 'check status');
    }
    return bytes;
}
export function replayRunSize(metadata: Metadata, config: RunConfig, initialBytes: Uint8Array, checks: readonly Check[], l: BinaryLimits): {
    bytes: number;
    items: number;
} { return auditRunLayout(metadata, config, initialBytes, checks, l); }
export function replayStepPreflight(inputLength: number, disposition: Disposition, outputCount: number, checks: readonly Check[], l: BinaryLimits): {
    payloadBytes: number;
    items: number;
} {
    natural(inputLength, 'inputLength');
    natural(outputCount, 'outputCount');
    limit(inputLength <= Math.min(U32, l.max_blob_bytes), 'blob bytes');
    limit(outputCount <= Math.min(U32, l.max_outputs_per_step), 'outputs per step');
    const checksBytes = batchSize(checks, l), items = sum(1, outputCount, checks.length);
    limit(items <= l.max_items, 'aggregate items');
    let reason = 0;
    if (disposition.kind === 'accepted')
        limit(disposition.reason === '', 'accepted reason');
    else if (disposition.kind === 'rejected' || disposition.kind === 'ignored')
        reason = stringSize(disposition.reason, l);
    else
        limit(false, 'disposition tag');
    const payloadBytes = sum(21, inputLength, reason, outputCount * 4, checksBytes);
    limit(payloadBytes <= Math.min(U32, l.max_frame_bytes), 'frame bytes');
    return { payloadBytes, items };
}
export function effectiveBlobLimit(l: BinaryLimits): number { return Math.min(U32, l.max_blob_bytes, l.max_frame_bytes, l.max_total_bytes); }
export function errorFooterReserve(l: BinaryLimits): number { return 22 + Math.min(256, l.max_string_bytes, Math.max(0, l.max_frame_bytes - 13)); }
export function truncatedErrorReason(error:unknown,l:BinaryLimits):string {
    let text:string;
    try{text=String(error instanceof Error?error.message:error);}catch{text='unprintable model error';}
    const maximum=Math.min(256,l.max_string_bytes,Math.max(0,l.max_frame_bytes-13));
    function* characters():Generator<string>{
        for(let i=0;i<text.length;i++){
            const n=text.charCodeAt(i);
            if(n>=0xd800&&n<=0xdbff){
                const next=text.charCodeAt(i+1);
                if(next>=0xdc00&&next<=0xdfff){yield text.slice(i,i+2);i++;continue;}
            }else if(n<0xdc00||n>0xdfff){yield text[i];continue;}
            // Host diagnostics may contain invalid UTF-16. Escape those units explicitly;
            // supplied artifact text remains subject to strict scalar validation.
            for(const c of '\\u'+n.toString(16).padStart(4,'0'))yield c;
        }
    }
    let complete='',size=0;
    for(const c of characters()){
        const n=new TextEncoder().encode(c).length;
        if(size+n>maximum){
            const suffix=maximum>=3?'...':'',cap=maximum-suffix.length;let out='',used=0;
            for(const part of complete){const width=new TextEncoder().encode(part).length;if(used+width>cap)break;out+=part;used+=width;}
            return out+suffix;
        }
        complete+=c;size+=n;
    }
    return complete;
}
export class AuditBudget {
    bytes: number;
    items: number;
    steps = 0;
    readonly blobLimit: number;
    constructor(metadata: Metadata, config: RunConfig, initial: Uint8Array, checks: readonly Check[], readonly limits: BinaryLimits, footer = 18) {
        const run = replayRunSize(metadata, config, initial, checks, limits);
        this.bytes = sum(12, run.bytes);
        this.items = run.items;
        this.blobLimit = effectiveBlobLimit(limits);
        this.ensureFooter(footer);
    }
    ensureFooter(footer: number): void { limit(sum(this.bytes, footer) <= this.limits.max_total_bytes, 'total bytes'); limit(footer - 9 <= this.limits.max_frame_bytes, 'frame bytes'); }
    /** The Rust recorder charges each output length immediately before its codec.
     * Rich replay deliberately uses plan() instead, reserving every fixed field up front. */
    planRecord(input:Uint8Array,disposition:Disposition,outputCount:number,checks:readonly Check[],footer=18):AuditStepBudget {
        limit(this.steps<this.limits.max_steps,'steps');
        natural(outputCount,'outputCount');
        limit(outputCount<=Math.min(U32,this.limits.max_outputs_per_step),'outputs per step');
        const items=sum(1,outputCount,checks.length);
        limit(sum(this.items,items)<=this.limits.max_items,'aggregate items');
        const fixed=replayStepPreflight(input.length,disposition,0,checks,this.limits);
        const available=Math.min(U32,this.limits.max_frame_bytes,this.limits.max_total_bytes-this.bytes-footer-9);
        limit(available>=fixed.payloadBytes,'frame bytes');
        return new AuditStepBudget(this,fixed.payloadBytes,items,available-fixed.payloadBytes);
    }
    plan(input: Uint8Array, disposition: Disposition, outputCount: number, checks: readonly Check[], footer = 18): AuditStepBudget {
        limit(this.steps < this.limits.max_steps, 'steps');
        const fixed = replayStepPreflight(input.length, disposition, outputCount, checks, this.limits);
        limit(sum(this.items, fixed.items) <= this.limits.max_items, 'aggregate items');
        const available = Math.min(U32, this.limits.max_frame_bytes, this.limits.max_total_bytes - this.bytes - footer - 9);
        limit(available >= fixed.payloadBytes, 'frame bytes');
        return new AuditStepBudget(this, fixed.payloadBytes, fixed.items, available - fixed.payloadBytes);
    }
}
export class AuditStepBudget {
    private bodies = 0;
    constructor(private owner: AuditBudget, private fixed: number, private items: number, private remaining: number) { }
    get maximum(): number { return Math.min(this.owner.blobLimit, this.remaining); }
    reserveOutput():void {limit(this.remaining>=4,'output frame bytes');this.remaining-=4;this.fixed=sum(this.fixed,4);}
    accept(bytes: Uint8Array): void { limit(bytes.length <= this.maximum, 'blob bytes'); this.remaining -= bytes.length; this.bodies = sum(this.bodies, bytes.length); }
    commit(footer = 18): void { const next = sum(this.owner.bytes, 9, this.fixed, this.bodies); limit(sum(next, footer) <= this.owner.limits.max_total_bytes, 'total bytes'); this.owner.bytes = next; this.owner.items = sum(this.owner.items, this.items); this.owner.steps++; }
}
