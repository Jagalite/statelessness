use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] enum E {#[trace(tag=1)] A,#[trace(tag=1)] B}
fn main(){}
