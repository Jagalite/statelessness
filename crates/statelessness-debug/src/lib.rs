//! Optional, dependency-free transition debugging. Effects remain host-owned.
//! Model values require no new Debug, Send, Sync, serde, or codec bounds.

pub mod session;
pub use session::{DebugError, DebugSession, InputPolicy, SessionLimits};

pub mod workbench;

pub mod inspect;

pub mod live;

pub mod watches;

pub mod diagnostic;
pub mod protocol;

pub mod effects;
pub mod metrics;
