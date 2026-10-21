use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] struct S {#[trace(skip)] v:u8}
fn main(){}
