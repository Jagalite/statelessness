/** Explicit Node-only private new-file exports. Core evidence APIs remain browser-compatible. */
// @ts-ignore Node built-in; the browser core does not depend on Node declarations.
import { openSync, writeSync, fsyncSync, closeSync } from 'node:fs';
import { ModelError } from './errors.js';
import { writeTraceV2, diagnosticJSON, type TraceV2, type EvidenceLimitOptions, type Artifact, type ExportPolicy } from './evidence.js';
function privateNew(path: string, bytes: Uint8Array): void {
    const fd: number = openSync(path, 'wx', 0o600);
    let primary: unknown;
    try {
        let offset = 0;
        while (offset < bytes.length) {
            const n: number = writeSync(fd, bytes, offset, bytes.length - offset);
            if (!Number.isSafeInteger(n) || n <= 0 || n > bytes.length - offset)
                throw new Error('invalid file write count');
            offset += n;
        }
        fsyncSync(fd);
    }
    catch (e) {
        primary = e;
    }
    try {
        closeSync(fd);
    }
    catch (e) {
        if (primary === undefined)
            primary = e;
    }
    if (primary !== undefined)
        throw new ModelError(`incomplete newly created evidence file: ${primary instanceof Error ? primary.message : String(primary)}`);
}
/** Precomputes bounded bytes, creates exclusively mode0600 on Unix, fsyncs file; no rename/directory sync. */
export function writeRawEvidenceNew(path: string, evidence: TraceV2, limits: EvidenceLimitOptions = {}): void { const bytes = writeTraceV2(evidence, limits); privateNew(path, bytes); }
export function exportDiagnosticNew(path: string, artifact: Artifact, policy: ExportPolicy = {}): void { const bytes = diagnosticJSON(artifact, policy); privateNew(path, bytes); }
