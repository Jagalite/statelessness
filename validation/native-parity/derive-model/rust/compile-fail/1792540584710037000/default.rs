use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceDecode)] struct S {#[trace(default)] v:u8}
fn main(){}
