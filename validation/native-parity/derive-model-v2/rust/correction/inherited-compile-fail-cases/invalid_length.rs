use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] struct S {#[trace(length="u64")] v:String}
fn main(){}
