use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(TraceEncode)] #[trace(tag_type="u16")] struct S;
fn main(){}
