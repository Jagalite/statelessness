use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] struct S {#[trace(length="u8",with="adapter")] v:String}
fn main(){}
