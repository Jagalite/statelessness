use statelessness_macros::{TraceEncode,TraceDecode};
mod adapter {} #[derive(TraceDecode)] struct S(#[trace(with="adapter")] u8);
fn main(){}
