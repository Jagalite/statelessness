use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=0)] struct S;
fn main(){}
