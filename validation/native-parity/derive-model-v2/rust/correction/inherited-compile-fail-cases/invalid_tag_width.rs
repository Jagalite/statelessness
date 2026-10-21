use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] #[trace(tag_type="u64")] enum E {#[trace(tag=1)] A}
fn main(){}
