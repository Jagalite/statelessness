use statelessness_macros::{TraceEncode,TraceDecode};
#[derive(statelessness_macros::ModelCodec)] #[model_codec(unknown)] struct S;
fn main(){}
