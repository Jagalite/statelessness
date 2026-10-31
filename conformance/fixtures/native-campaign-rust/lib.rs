//! A private source-bound fixture, never a supported campaign API.
#![allow(dead_code)]
pub use stateless::*;
include!(concat!(env!("OUT_DIR"), "/entry.rs"));
