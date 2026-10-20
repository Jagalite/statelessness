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
