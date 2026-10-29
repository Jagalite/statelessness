export * from './core.js';
export * from './oracle.js';
export { FormatError, parseTrace, stringifyTrace, traceFromData, traceToData } from './json.js';

export * from './search.js';
export {DEFAULT_BINARY_LIMITS,BinaryError,auditCRC32,auditTraceFromPortable,auditTraceToPortable,readAuditTrace,writeAuditTrace,writeAuditTraceTo,resolveBinaryLimits,validateAuditTrace,snapshotAuditTrace,auditRunLayout} from './binary.js';
export type {RunConfig,AuditTrace,BinaryLimits,BinaryErrorKind,AuditReader,AuditWriter} from './binary.js';
export {recordAudit} from './audit_record.js';

export {replayWithObservations,replayWithObservationsBounded,ReplayError} from './replay.js';
export type {ReplayOptions,ReplayDifferences,ReplayActual,ReplayObservation,ReplayObserver} from './replay.js';
export {CheckedInitial,CheckedTurn,checkInitial,checkTurn,checkTurnExact,FULL_CHECKS_EXACT} from './checked.js';
export type {ExactCheckPolicy,TurnObservation} from './checked.js';
export {Recorder} from './runtime_recorder.js';
export type {RecorderOptions} from './runtime_recorder.js';
export {DEFAULT_EVIDENCE_LIMITS,resolveEvidenceLimits,emptyCausalContext,validateTraceV2,snapshotTraceV2,writeTraceV2,writeTraceV2To,readArtifact,artifactTrace,checkPhase,replayTraceV2,verifyUpgrade,diagnosticJSON} from './evidence.js';
export type {Provenance,OutputOrigin,CausalContext,StepEvidence,TraceV2,Artifact,EvidenceLimits,EvidenceLimitOptions,EvidenceCheckPhase,ExportPolicy} from './evidence.js';
export {EvidenceRecorder} from './evidence_recorder.js';
export {Decoder,DEFAULT_DECODE_LIMITS,resolveDecodeLimits,logicalLayout,customValueSchema,encodeValue,decodeValue,UNIT,NONE,some,withLength,encodeLengthValue,valueCodecs} from './value_codec.js';
export type {DecodeLimits,ResolvedDecodeLimits,LogicalLayout,ValueSchema,LengthSchema,CustomValueSchema,Unit,Option,SchemaValue,TupleValue} from './value_codec.js';

// Explicit opt-in finite research; no production exploration settings change.
export {compareEquivalence,qualificationParameters,DEFAULT_EXPERIMENT_CONFIG} from './research.js';
export type {Assumptions,Proposal,ExperimentConfig,ExperimentStatus,RequiredProperty,PropertyPhase,ViolationWitness,Reachability,EquivalenceCounterexample,ExperimentReport,ResearchEquality,ResearchModel} from './research.js';

export {codecField,orderedRecord,tupleRecord,unitRecord,enumCases,taggedEnum,bindModelCodec} from './value_codec.js';
export type {ValueEncoder,ValueDecoder,LengthEncoder,LengthDecoder,CodecField,RecordDeclaration,EnumCase} from './value_codec.js';
export {DomainDescriptor,Domain,checkLazy,declaredProperties,adaptModel,type PropertyMetadata} from './modeling.js';
