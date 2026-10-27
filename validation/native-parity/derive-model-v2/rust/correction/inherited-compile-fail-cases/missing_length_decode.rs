use statelessness_macros::{TraceEncode,TraceDecode};
struct No; #[derive(TraceDecode)] struct S(#[trace(length="u8")] No);
fn main(){}
