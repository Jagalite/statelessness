use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=1,version=2)] struct S;
fn main(){}
