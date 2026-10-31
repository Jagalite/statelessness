use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] #[trace(tag_type="u32")] enum E {#[trace(tag=4294967296)] A}
fn main(){}
