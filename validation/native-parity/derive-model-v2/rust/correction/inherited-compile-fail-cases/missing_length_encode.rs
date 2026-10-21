use statelessness_macros::{TraceEncode,TraceDecode};
struct No; #[derive(TraceEncode)] struct S(#[trace(length="u8")] No);
fn main(){}
