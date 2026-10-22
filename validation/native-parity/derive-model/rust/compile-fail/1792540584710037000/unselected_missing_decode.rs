use statelessness_macros::{TraceEncode,TraceDecode};
struct No; #[derive(TraceDecode)] enum E {#[trace(tag=1)] Unit,#[trace(tag=2)] Other(No)}
fn main(){}
