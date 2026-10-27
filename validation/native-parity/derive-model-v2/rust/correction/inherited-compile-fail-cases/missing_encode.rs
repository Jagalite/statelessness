use statelessness_macros::{TraceEncode,TraceDecode};
struct No; #[derive(TraceEncode)] struct S(No);
fn main(){}
