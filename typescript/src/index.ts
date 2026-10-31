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
export {LogLevel,SnapshotPolicy,DEFAULT_OBSERVATION_OPTIONS,NoPayloads,EncodedPayloads,TextObserver,TextObservationError} from './observation.js';
export type {Event,Observer,ObservationOptions,PayloadFormat,PayloadWriter,ObservationWriter,TextObservationErrorStage} from './observation.js';
export {DEFAULT_EVIDENCE_LIMITS,resolveEvidenceLimits,emptyCausalContext,validateTraceV2,snapshotTraceV2,writeTraceV2,writeTraceV2To,readArtifact,artifactTrace,checkPhase,replayTraceV2,verifyUpgrade,diagnosticJSON} from './evidence.js';
export type {Provenance,OutputOrigin,CausalContext,StepEvidence,TraceV2,Artifact,EvidenceLimits,EvidenceLimitOptions,EvidenceCheckPhase,ExportPolicy} from './evidence.js';
export {EvidenceRecorder} from './evidence_recorder.js';
export {Decoder,DEFAULT_DECODE_LIMITS,resolveDecodeLimits,logicalLayout,customValueSchema,encodeValue,decodeValue,UNIT,NONE,some,withLength,encodeLengthValue,valueCodecs} from './value_codec.js';
export type {DecodeLimits,ResolvedDecodeLimits,LogicalLayout,ValueSchema,LengthSchema,CustomValueSchema,Unit,Option,SchemaValue,TupleValue} from './value_codec.js';

// Explicit opt-in finite research; no production exploration settings change.
export {compareEquivalence,compareEquivalenceBounded,qualificationParameters,DEFAULT_EXPERIMENT_CONFIG,DEFAULT_RESEARCH_COMPARISON_LIMITS} from './research.js';
export type {Assumptions,Proposal,ExperimentConfig,ExperimentStatus,RequiredProperty,PropertyPhase,ViolationWitness,Reachability,EquivalenceCounterexample,ExperimentReport,ResearchEquality,ResearchModel,ResearchComparisonLimits,BoundedExperimentReport} from './research.js';

export {codecField,orderedRecord,tupleRecord,unitRecord,enumCases,taggedEnum,bindModelCodec} from './value_codec.js';
export type {ValueEncoder,ValueDecoder,LengthEncoder,LengthDecoder,CodecField,RecordDeclaration,EnumCase} from './value_codec.js';

export type {DebugObservation} from './observation.js';

export { GUIDANCE_ALGORITHM, DEFAULT_GUIDANCE, FeatureBuffer, StateFeedback, exactStateFeedback, validateGuidanceConfig, fuzzGuided, fuzzGuidedAuto, fuzzGuidedObserved } from './guided.js';
export type { FeedbackMetadata, FeatureIdentity, Feedback, GuidanceConfig, CorpusEntry, GuidanceReport, GuidedFuzzReport } from './guided.js';

export { DEFAULT_CAMPAIGN, CampaignCancellation, jobSeed, validateCampaignConfig, runCampaign, runGuidedCampaign, runAutoCampaign, runGuidedAutoCampaign } from './campaign.js';
export type { CampaignConfig, CampaignLimits, CampaignModel, AutoCampaignModel, CampaignFactory, CampaignFeedback, JobOutcome, JobReport, CampaignSink, CampaignReport, CampaignTermination } from './campaign.js';

export {DomainDescriptor,Domain,checkLazy,declaredProperties,adaptModel,type PropertyMetadata} from './modeling.js';

export * from './stateful.js';

export type { GuidanceConfig64, GuidanceReport64, GuidedFuzzReport64 } from './guided.js';
export type { CampaignConfig64, JobReport64, CampaignSink64, CampaignReport64 } from './campaign.js';

export {ASSURANCE_VERSION,validateAssuranceManifest,validateAssuranceReport,mergeAssuranceReports,assessAssuranceReport,assessMergedAssurance,assuranceManifestJSON,assuranceReportJSON,mergedAssuranceJSON,parseAssuranceManifestJSON,parseAssuranceReportJSON,parseMergedAssuranceJSON,AssuranceObserver,assuredEnumerate,assuredFuzz,assuredGuidedFuzz,assuredAutoFuzz,assuredGuidedAutoFuzz,assuredCampaign,assuredGuidedCampaign,assuredAutoCampaign,assuredGuidedAutoCampaign} from './assurance.js';
export type {AssurancePhase,AssuranceProperty,AssuranceFamilyCoverage,AssuranceFamily,AssuranceGoal,AssuranceManifest,AssuranceOutcome,AssuranceCounts,AssuranceReport,AssuranceHits,AssuranceCoverage,AssuredRun,MergedAssurance,AssuranceRetention,AssuranceCampaignOptions,AssuranceJSONLimits,AssuranceObservationLimits,AssuranceAssessment,AssuranceSnapshotLimits} from './assurance.js';
export {DEFAULT_REFERENCE_BOUNDS,REFERENCE_MUTATIONS,REFERENCE_REQUIREMENT_FINGERPRINTS,ReferenceController,referenceInitialState,reduceReference,encodeReferenceState,decodeReferenceState,encodeReferenceInput,decodeReferenceInput,encodeReferenceOutput,referenceAssuranceManifest,referenceCoverage} from './reference_controller.js';
export type {ReferenceOutcome,ReferenceSettlement,ReferencePhase,ReferenceJob,ReferenceState,ReferenceInput,ReferenceEffect,ReferenceBounds,ReferenceMutation} from './reference_controller.js';
export {ReferenceHost} from './reference_host.js';
export type {ReferenceHostOptions} from './reference_host.js';
export {REFERENCE_MUTATION_TARGETS,evaluateReferenceMutation} from './reference_evaluation.js';
export type {ReferenceMutationResult,ReferenceEvaluationLimits} from './reference_evaluation.js';
export {validateReferenceRequirementCatalog,parseReferenceCorpus,checkReferenceRegressions,prepareReferenceMutationSuite,verifyReferenceSuiteArtifacts,assessReferenceSuiteArtifacts} from './reference_evidence.js';
export type {ReferenceWitness,ReferenceSuite,ReferenceSuiteOptions,ReferenceSuiteVerificationOptions} from './reference_evidence.js';

export {RequestModel} from './demo.js';
export type {RequestState,RequestInput,RequestOutput} from './demo.js';

export {RUNTIME_SOURCE_BUILD_ID} from './runtime_identity.js';
