use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] struct S {#[trace(length="u8",length="u16")] v:String}
fn main(){}
