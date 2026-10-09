//! Application-owned state, deterministic transitions, properties, and evidence.
//!
//! The engine never executes an application's external effects. Models describe
//! those effects as data and supply their outcomes as explicit inputs.
//!
//! Implement [`Model`] for your application, then add [`Enumerate`] for finite
//! exploration and [`ModelCodec`] for durable evidence. The repository's
//! [application starter](https://github.com/Jagalite/statelessness/blob/main/examples/README.md)
//! provides a complete custom-model test and replay executable.
//!
//! A regression gate must inspect findings, termination, and skipped checks:
//!
//! ```
//! use stateless::demo::RequestModel;
//! use stateless::explore::{enumerate, SearchConfig, SearchTermination};
//!
//! let report = enumerate(&RequestModel::fixed(), SearchConfig::default())?;
//! assert!(report.failure.is_none());
//! assert_eq!(report.termination, SearchTermination::GraphExhausted);
//! assert_eq!(report.skipped_checks, 0);
//! # Ok::<(), stateless::ModelError>(())
//! ```
//!
//! Persistence and replay use the same application model. Exact replay verifies
//! the recorded sequence, not the entire reachable graph:
//!
//! ```
//! use stateless::demo::RequestModel;
//! use stateless::execution::{record, replay, ReplayOptions, ReplayOutcome};
//! use stateless::explore::{enumerate, SearchConfig};
//! use stateless::trace::{ReadLimits, RunConfig, Trace};
//!
//! let model = RequestModel::buggy();
//! let failure = enumerate(&model, SearchConfig::default())?.failure.unwrap();
//! let steps = failure.inputs.len();
//! let trace = record(&model, failure.inputs, RunConfig::default(), steps)?;
//! let mut bytes = Vec::new();
//! trace.write_to(&mut bytes)?;
//! let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default())?;
//! let report = replay(&model, &restored, ReplayOptions::default())?;
//! assert_eq!(report.outcome, ReplayOutcome::Exact);
//! assert!(report.failure_reproduced);
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```

pub mod automatic;
pub mod campaign;
pub mod demo;
pub mod execution;
pub mod explore;
pub mod ffi;
pub mod guided;
pub mod model;
pub mod monitor;
pub mod observation;
pub mod oracle;
pub mod trace;

pub use model::*;

pub use oracle::{
    ObservationError, ObservationResult, Oracle, OracleCodec, OracleState, WithOracle,
};

pub mod composition;
pub mod lifecycle;
pub mod modeling;
pub mod value_codec;

pub mod conformance;
