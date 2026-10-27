use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] enum E {#[trace(tag=256)] A}
fn main(){}
