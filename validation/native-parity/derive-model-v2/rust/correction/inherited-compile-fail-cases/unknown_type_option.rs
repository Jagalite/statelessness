use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] #[trace(unknown)] struct S;
fn main(){}
