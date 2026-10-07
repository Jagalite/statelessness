//! Application-owned state, deterministic transitions, properties, and evidence.
//!
//! The engine never executes an application's external effects. Models describe
//! those effects as data and supply their outcomes as explicit inputs.

pub mod automatic;
pub mod campaign;
pub mod demo;
pub mod execution;
pub mod explore;
pub mod ffi;
pub mod guided;
pub mod model;
pub mod monitor;
pub mod oracle;
pub mod trace;

pub use model::*;

pub use oracle::{
    ObservationError, ObservationResult, Oracle, OracleCodec, OracleState, WithOracle,
};
