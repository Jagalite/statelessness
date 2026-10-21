use statelessness_macros::{TraceEncode,TraceDecode};
struct No; #[derive(TraceDecode)] struct S(No);
fn main(){}
