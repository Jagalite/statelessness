use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] enum E {#[trace(tag=1)] A=1}
fn main(){}
