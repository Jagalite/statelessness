"""Native Python Statelessness: deterministic checking without Rust or FFI."""
from .core import (
    Check, CheckPolicy, Disposition, EnumerateModel, Failure, Metadata, Model,
    ModelCodec, ModelError, ReplayReport, Rng, SearchConfig, SearchReport, Trace,
    TraceLimits, TraceStep, Transition, Violation, check_observed,
    enumerate_states, record, replay, validate_recording,
)
from .oracle import (EXPERIMENTAL_PROFILES, ObservationError, Oracle, OracleState,
                     Paired, Projection, WithOracle)

__version__ = "0.1.0"
SPEC_VERSION = "1.0.0"
PROFILES = ("core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1")
__all__ = [
    "Check", "CheckPolicy", "Disposition", "EnumerateModel", "Failure", "Metadata",
    "Model", "ModelCodec", "ModelError", "ReplayReport", "Rng", "SearchConfig",
    "SearchReport", "Trace", "TraceLimits", "TraceStep", "Transition", "Violation",
    "check_observed", "enumerate_states", "record", "replay", "validate_recording",
    "SPEC_VERSION", "PROFILES",
    "EXPERIMENTAL_PROFILES", "ObservationError", "Oracle", "OracleState",
    "Paired", "Projection", "WithOracle",
]

from .search import (EXHAUSTED, Auto, AutoOptions, Generated, GenerateModel, FuzzConfig,
                     FuzzReport, RunLimits, SearchObserver, ShrinkAttempt, ShrinkConfig,
                     ShrinkLimits, ShrinkOperation, ShrinkReport, fuzz, shrink)
__all__ += ["EXHAUSTED", "Auto", "AutoOptions", "Generated", "GenerateModel", "FuzzConfig",
            "FuzzReport", "RunLimits", "SearchObserver", "ShrinkAttempt", "ShrinkConfig",
            "ShrinkLimits", "ShrinkOperation", "ShrinkReport", "fuzz", "shrink"]

EXPERIMENTAL_PROFILES = EXPERIMENTAL_PROFILES + ("search-v1",)
