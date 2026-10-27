use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=4294967296)] struct S;
fn main(){}
