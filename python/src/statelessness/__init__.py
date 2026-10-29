"""Native Python Statelessness: deterministic checking without Rust or FFI."""
from .core import (
    Check, CheckPolicy, CheckSink, Disposition, EnumerateModel, Failure, Metadata, Model,
    ModelCodec, ModelError, ReplayReport, Rng, SearchConfig, SearchReport, Trace,
    TraceLimits, TraceStep, Transition, Violation, check_observed, check_observed_into,
    enumerate_states, record, replay, validate_recording,
)
from .oracle import (EXPERIMENTAL_PROFILES, ObservationError, Oracle, OracleState,
                     Paired, Projection, WithOracle)

__version__ = "0.1.0"
SPEC_VERSION = "1.0.0"
PROFILES = ("core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1")
__all__ = [
    "Check", "CheckPolicy", "CheckSink", "Disposition", "EnumerateModel", "Failure", "Metadata",
    "Model", "ModelCodec", "ModelError", "ReplayReport", "Rng", "SearchConfig",
    "SearchReport", "Trace", "TraceLimits", "TraceStep", "Transition", "Violation",
    "check_observed", "check_observed_into", "enumerate_states", "record", "replay", "validate_recording",
    "SPEC_VERSION", "PROFILES",
    "EXPERIMENTAL_PROFILES", "ObservationError", "Oracle", "OracleState",
    "Paired", "Projection", "WithOracle",
]

from .search import (EXHAUSTED, Auto, AutoOptions, Generated, GenerateModel, FuzzConfig,
                     FuzzReport, RunLimits, SearchLimits, SearchObserver, ShrinkAttempt, ShrinkConfig,
                     ShrinkLimits, ShrinkOperation, ShrinkReport, fuzz, shrink)
__all__ += ["EXHAUSTED", "Auto", "AutoOptions", "Generated", "GenerateModel", "FuzzConfig",
            "FuzzReport", "RunLimits", "SearchLimits", "SearchObserver", "ShrinkAttempt", "ShrinkConfig",
            "ShrinkLimits", "ShrinkOperation", "ShrinkReport", "fuzz", "shrink"]

from .guided import (CorpusEntry, Feedback, FeedbackMetadata, FeatureBuffer,
                     GuidanceConfig, GuidanceReport, StateFeedback, exact_state,
                     guided_fuzz)
__all__ += ["CorpusEntry", "Feedback", "FeedbackMetadata", "FeatureBuffer",
            "GuidanceConfig", "GuidanceReport", "StateFeedback", "exact_state", "guided_fuzz"]

EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("search-v1",)

from .trace_binary import (AuditTrace, BinaryLimits, BinaryTraceError, RunConfig,
                           audit_from_bytes, audit_to_bytes, read_audit, write_audit)
__all__ += ["AuditTrace", "BinaryLimits", "BinaryTraceError", "RunConfig",
            "audit_from_bytes", "audit_to_bytes", "read_audit", "write_audit"]
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("native-binary-v1",)

EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("bfs-controls-v1",)

EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("append-checks-v1",)

from .codec import EncodeBuffer
from .replay_evidence import (ReplayActual, ReplayDifferences, ReplayError,
    ReplayErrorObservation, ReplayInitialObservation, ReplayTurnObservation,
    replay_with_observations, replay_with_observations_bounded)
from .audit_record import record_audit
__all__ += ["EncodeBuffer", "ReplayActual", "ReplayDifferences", "ReplayError",
    "ReplayErrorObservation", "ReplayInitialObservation", "ReplayTurnObservation",
    "replay_with_observations", "replay_with_observations_bounded", "record_audit"]
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("encode-buffers-v1", "replay-evidence-v1")

from .checked import CheckedInitial, CheckedTurn, TurnObservation, check_initial, check_turn
__all__ += ["CheckedInitial", "CheckedTurn", "TurnObservation", "check_initial", "check_turn"]
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("checked-results-v1",)

from .monitor import Recorder, RecorderOptions
__all__ += ["Recorder", "RecorderOptions"]

from .evidence import (Artifact, CausalContext, EvidenceLimits, ExportPolicy,
    OutputOrigin, Provenance, StepEvidence, TraceV2, diagnostic_json,
    export_diagnostic_new)
from .evidence_recorder import EvidenceRecorder
__all__ += ["Artifact", "CausalContext", "EvidenceLimits", "ExportPolicy",
    "OutputOrigin", "Provenance", "StepEvidence", "TraceV2", "diagnostic_json",
    "export_diagnostic_new", "EvidenceRecorder"]
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("causal-evidence-v2",)

from . import value_codec
from .value_codec import (DecodeLimits, Decoder, Rust64Layout, Schema, Some,
    OptionOf, VectorOf, ArrayOf, TupleOf, OracleStateOf, encode_canonical,
    decode_canonical, encode_into, encode_raw, encode_length_raw, decode_length_raw, U8, U16, U32, U64, U128, I8, I16, I32,
    I64, I128, BOOL, UNIT, STRING)
__all__ += ["value_codec", "DecodeLimits", "Decoder", "Rust64Layout", "Schema",
    "Some", "OptionOf", "VectorOf", "ArrayOf", "TupleOf", "OracleStateOf",
    "encode_canonical", "decode_canonical", "encode_into", "encode_raw", "encode_length_raw", "decode_length_raw", "U8", "U16", "U32",
    "U64", "U128", "I8", "I16", "I32", "I64", "I128", "BOOL", "UNIT", "STRING"]
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("canonical-value-codec-v1",)

# Opt-in finite research tooling; production pruning remains disabled.
EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("research-equivalence-v1",)

from .codec_declarations import (DECLARATION_API_V1, adapter, field,
    positional_field, adapted_field, ordered_record, tuple_record, unit_record,
    case, tagged_enum, ModelCodec as ModelCodecBinding, model_codec)
__all__ += ["DECLARATION_API_V1", "adapter", "field", "positional_field",
    "adapted_field", "ordered_record", "tuple_record", "unit_record", "case",
    "tagged_enum", "ModelCodecBinding", "model_codec"]
from .campaign import (CampaignConfig, CampaignReport, JobOutcome, JobReport,
    CampaignCancellation, InputTransfer, PickleInputTransfer, ProcessWorkers,
    job_seed, run_campaign, run_guided_campaign)
__all__ += ["CampaignConfig", "CampaignReport", "JobOutcome", "JobReport",
    "CampaignCancellation", "InputTransfer", "PickleInputTransfer", "ProcessWorkers",
    "job_seed", "run_campaign", "run_guided_campaign"]
