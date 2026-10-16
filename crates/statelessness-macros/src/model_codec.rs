//! Opt-in codec glue for handwritten Model implementations. No field inspection
//! or model-property inference: associated values supply the existing traits.
use crate::parse::*;
use proc_macro::{Delimiter, TokenStream, TokenTree};

pub fn expand(input: TokenStream) -> Result<String> {
    let ts = tokens(input);
    let mut p = 0;
    let attrs = attributes(&ts, &mut p)?;
    let mut rt = "::stateless".to_string();
    let mut limits = None;
    let mut version = 1u32;
    let mut seen = std::collections::BTreeSet::new();
    for (a, options_tokens) in attrs {
        if a != "model_codec" {
            continue;
        }
        for (key, value) in options(&options_tokens)? {
            if !seen.insert(key.clone()) {
                return Err(error("SM400: duplicate model_codec option"));
            }
            match key.as_str() {
                "crate" => rt = literal(&value)?,
                "decode_limits" => limits = Some(value),
                "version" => {
                    version = value.parse().map_err(|_| {
                        error("SM401: codec version must be a positive u32 literal")
                    })?;
                    if version == 0 {
                        return Err(error("SM401: codec version must be positive"));
                    }
                }
                _ => return Err(error("SM400: unknown model_codec option")),
            }
        }
    }
    if ts.get(p).is_some_and(|t| is(t, "pub")) {
        p += 1;
        if matches!(ts.get(p), Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis)
        {
            p += 1;
        }
    }
    if !ts.get(p).is_some_and(|t| is(t, "struct")) {
        return Err(error("SM402: ModelCodec derive requires a model struct"));
    }
    p += 1;
    let name = ts
        .get(p)
        .ok_or_else(|| error("SM402: missing model struct name"))?
        .to_string();
    p += 1;
    let h = header(&ts[p..])?;
    let mut tail = h.tail;
    // Tuple fields are the leading group; named fields are the final brace
    // group. Parentheses inside a where bound (for example Fn()) are not fields.
    let body = if matches!(tail.first(), Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis)
    {
        Some(0)
    } else {
        tail.iter()
            .rposition(|t| matches!(t, TokenTree::Group(g) if g.delimiter() == Delimiter::Brace))
    };
    if let Some(index) = body {
        tail.remove(index);
    }
    tail.retain(|t| !is(t, ";"));
    let existing = text(&tail);
    let existing = existing.trim_end_matches(',').trim();
    let where_clause = if existing.is_empty() {
        "where".into()
    } else {
        format!("{existing},")
    };
    let limits = limits.unwrap_or_else(|| format!("{rt}::value_codec::DecodeLimits::default()"));
    let check = format!(
        "if <Self as {rt}::Model>::metadata(self).codec_version != {version} {{return ::std::result::Result::Err({rt}::ModelError::new(\"ModelCodec derive version does not match Model metadata\"));}}"
    );
    let mut methods = String::new();
    for (part, ty) in [("state", "State"), ("input", "Input"), ("output", "Output")] {
        methods.push_str(&format!("fn encode_{part}(&self,value:&Self::{ty})->::std::result::Result<::std::vec::Vec<::std::primitive::u8>,{rt}::ModelError>{{{check}{rt}::value_codec::TraceEncode::trace_bytes(value,::std::primitive::usize::MAX)}} fn encode_{part}_into(&self,value:&Self::{ty},out:&mut {rt}::EncodeBuffer<'_>)->::std::result::Result<(),{rt}::ModelError>{{{check}{rt}::value_codec::TraceEncode::trace_encode(value,out)}}"));
        if part != "output" {
            methods.push_str(&format!("fn decode_{part}(&self,bytes:&[::std::primitive::u8])->::std::result::Result<Self::{ty},{rt}::ModelError>{{{check}{rt}::value_codec::TraceDecode::from_trace(bytes,{limits})}}"));
        }
    }
    Ok(format!(
        "const _: () = {rt}::modeling::MACRO_API_V1; impl {} {rt}::ModelCodec for {name}{} {where_clause} Self: {rt}::Model, <Self as {rt}::Model>::State: {rt}::value_codec::TraceEncode+{rt}::value_codec::TraceDecode, <Self as {rt}::Model>::Input: {rt}::value_codec::TraceEncode+{rt}::value_codec::TraceDecode, <Self as {rt}::Model>::Output: {rt}::value_codec::TraceEncode {{ {methods} }}",
        h.generics, h.args
    ))
}
