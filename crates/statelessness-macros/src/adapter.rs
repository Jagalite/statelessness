use crate::parse::*;
use proc_macro::{Delimiter, TokenStream, TokenTree};
pub fn expand(args: TokenStream, item: TokenStream) -> Result<String> {
    let mut rt = "::stateless".to_string();
    let mut state = None;
    let mut input = None;
    let mut output = None;
    let mut unchecked = false;
    let mut codec = false;
    let mut decode_limits = format!("{rt}::value_codec::DecodeLimits::default()");
    let mut seen = std::collections::BTreeSet::new();
    for (k, v) in options(&tokens(args))? {
        if !seen.insert(k.clone()) {
            return Err(error("SM200: duplicate model option"));
        }
        match k.as_str() {
            "crate" => rt = literal(&v)?,
            "state" => state = Some(v),
            "input" => input = Some(v),
            "output" => output = Some(v),
            "decode_limits" => decode_limits = v,
            "unchecked" if v.is_empty() => unchecked = true,
            "codec" if v.is_empty() => codec = true,
            _ => return Err(error("SM200: unknown model option")),
        }
    }
    if seen.contains("decode_limits") && !codec {
        return Err(error("SM200: decode_limits requires codec"));
    }
    if !seen.contains("decode_limits") {
        decode_limits = format!("{rt}::value_codec::DecodeLimits::default()");
    }
    let (state, input, output) = (
        state.ok_or_else(|| error("SM201: state required"))?,
        input.ok_or_else(|| error("SM201: input required"))?,
        output.ok_or_else(|| error("SM201: output required"))?,
    );
    let ts = tokens(item);
    if !ts.first().is_some_and(|t| is(t, "impl")) {
        return Err(error("SM202: model requires an inherent impl"));
    }
    let h = header(&ts[1..])?;
    let mut tail = h.tail;
    let body = tail
        .pop()
        .ok_or_else(|| error("SM202: impl body required"))?;
    let body = group(&body, Delimiter::Brace)?;
    if tail.iter().any(|t| is(t, "for")) {
        return Err(error("SM202: trait impls are unsupported"));
    }
    let wp = tail
        .iter()
        .position(|t| is(t, "where"))
        .unwrap_or(tail.len());
    let target = text(&tail[..wp]);
    let where_clause = text(&tail[wp..]);
    let bt = tokens(body.stream());
    let mut pos = 0;
    let mut clean = Vec::new();
    let mut roles = std::collections::BTreeMap::new();
    let mut checks: Vec<(String, String, String)> = vec![];
    let mut ids = std::collections::BTreeSet::new();
    while pos < bt.len() {
        let start = pos;
        let attrs = attributes(&bt, &mut pos)?;
        let has_inline = attrs.iter().any(|(name, _)| name == "inline");
        let mut role = None;
        for (a, v) in attrs {
            if a == "stateless" {
                if role.is_some() {
                    return Err(error("SM203: one role per method"));
                }
                let name = v
                    .first()
                    .ok_or_else(|| error("SM203: empty callback role"))?
                    .to_string();
                let mut id = None;
                if v.len() > 1 {
                    let g = group(&v[1], Delimiter::Parenthesis)?;
                    if v.len() != 2 {
                        return Err(error("SM203: malformed role"));
                    }
                    for (k, v) in options(&tokens(g.stream()))? {
                        if k != "id" || id.is_some() {
                            return Err(error("SM204: expected one literal property id"));
                        }
                        literal(&v)?;
                        id = Some(v);
                    }
                }
                role = Some((name, id));
            } else if a == "cfg" || a == "cfg_attr" {
                return Err(error("SM205: conditional model methods are unsupported"));
            }
        }
        for pair in bt[start..pos].chunks(2) {
            if let Some(TokenTree::Group(g)) = pair.get(1)
                && !tokens(g.stream())
                    .first()
                    .is_some_and(|t| is(t, "stateless"))
            {
                clean.extend_from_slice(pair);
            }
        }
        if role.is_some() && !has_inline {
            clean.extend(tokens(
                "#[inline]".parse().expect("static inline attribute"),
            ));
        }
        let method_start = pos;
        if bt.get(pos).is_some_and(|t| is(t, "pub")) {
            pos += 1;
            if matches!(bt.get(pos),Some(TokenTree::Group(g)) if g.delimiter()==Delimiter::Parenthesis)
            {
                pos += 1;
            }
        }
        while bt
            .get(pos)
            .is_some_and(|t| ["async", "const", "unsafe"].iter().any(|word| is(t, word)))
        {
            pos += 1;
        }
        if !bt.get(pos).is_some_and(|t| is(t, "fn")) {
            return Err(error_at(
                &bt[method_start],
                "SM206: only ordinary methods supported inside model impl",
            ));
        }
        let name = bt
            .get(pos + 1)
            .ok_or_else(|| error("SM206: missing method name"))?
            .to_string();
        let parameters = bt
            .get(pos + 2)
            .ok_or_else(|| error("SM207: generic callbacks unsupported"))?;
        let params = group(parameters, Delimiter::Parenthesis)?;
        let params = tokens(params.stream());
        if role.is_some() {
            let first = split(&params, ",")
                .first()
                .map(|x| text(x))
                .unwrap_or_default();
            if first != "& self" {
                return Err(error("SM207: callback must take &self"));
            }
        }
        while pos < bt.len()
            && !matches!(&bt[pos],TokenTree::Group(g) if g.delimiter()==Delimiter::Brace)
        {
            pos += 1;
        }
        if pos == bt.len() {
            return Err(error("SM206: method body required"));
        }
        pos += 1;
        clean.extend_from_slice(&bt[method_start..pos]);
        if let Some((r, id)) = role {
            if r == "state_check" || r == "transition_check" {
                let id = id.ok_or_else(|| error("SM204: checks require id = \"stable.name\""))?;
                if !ids.insert(literal(&id)?) {
                    return Err(error_at(&bt[start], "SM208: duplicate property id"));
                }
                checks.push((r, id, name));
            } else {
                if id.is_some() {
                    return Err(error("SM209: callback role does not accept id"));
                }
                if ![
                    "metadata",
                    "initial",
                    "step",
                    "estimated_state_bytes",
                    "inputs",
                    "state_checks",
                    "transition_checks",
                ]
                .contains(&r.as_str())
                {
                    return Err(error("SM209: unknown callback role"));
                }
                if roles.insert(r, name).is_some() {
                    return Err(error_at(&bt[start], "SM210: duplicate callback role"));
                }
            }
        }
        if pos == start {
            return Err(error("SM206: invalid method"));
        }
    }
    if checks.is_empty()
        && !roles.contains_key("state_checks")
        && !roles.contains_key("transition_checks")
        && !unchecked
    {
        return Err(error("SM211: no properties; explicitly select unchecked"));
    }
    let metadata = roles
        .get("metadata")
        .ok_or_else(|| error("SM212: metadata callback required"))?;
    let initial = roles
        .get("initial")
        .ok_or_else(|| error("SM212: initial callback required"))?;
    let step = roles
        .get("step")
        .ok_or_else(|| error("SM212: step callback required"))?;
    let mut methods = format!(
        "type State={state};type Input={input};type Output={output};#[inline] fn metadata(&self)->{rt}::ModelMetadata{{let mut m=Self::{metadata}(self);m.build.push_str({stamp:?});m.build.push_str(\";runtime:\");m.build.push_str({rt}::modeling::RUNTIME_BUILD_ID);m}} #[inline] fn initial_state(&self)->::std::result::Result<Self::State,{rt}::ModelError>{{Self::{initial}(self)}} #[inline] fn step(&self,state:&Self::State,input:&Self::Input)->::std::result::Result<{rt}::Transition<Self::State,Self::Output>,{rt}::ModelError>{{Self::{step}(self,state,input)}}",
        stamp = format!(";macro-v1:{}", env!("STATELESS_BUILD_ID"))
    );
    for (phase, params, call) in [
        ("state", "state:&Self::State", "state"),
        (
            "transition",
            &format!(
                "before:&Self::State,input:&Self::Input,transition:&{rt}::TransitionRef<'_,Self::State,Self::Output>"
            ),
            "before,input,transition",
        ),
    ] {
        let mut body = checks
            .iter()
            .filter(|(r, _, _)| r == &format!("{phase}_check"))
            .map(|(_, id, n)| {
                format!(
                    "checks.push({rt}::Check{{id:({id}).into(),status:Self::{n}(self,{call})?}});"
                )
            })
            .collect::<String>();
        if let Some(n) = roles.get(&format!("{phase}_checks")) {
            body.push_str(&format!("Self::{n}(self,{call},checks)?;"));
        }
        methods.push_str(&format!("#[inline] fn check_{phase}(&self,{params})->::std::result::Result<::std::vec::Vec<{rt}::Check>,{rt}::ModelError>{{let mut checks=::std::vec::Vec::new();<Self as {rt}::Model>::check_{phase}_into(self,{call},&mut {rt}::CheckSink::new(&mut checks))?;::std::result::Result::Ok(checks)}} #[inline] fn check_{phase}_into(&self,{params},checks:&mut {rt}::CheckSink<'_>)->::std::result::Result<(),{rt}::ModelError>{{{body} ::std::result::Result::Ok(())}}"));
    }
    if let Some(n) = roles.get("estimated_state_bytes") {
        methods.push_str(&format!("#[inline] fn estimated_state_bytes(&self,state:&Self::State)->::std::option::Option<::std::primitive::usize>{{Self::{n}(self,state)}}"));
    }
    let mut extra = String::new();
    if let Some(n) = roles.get("inputs") {
        extra.push_str(&format!("impl {} {rt}::Enumerate for {target} {where_clause} {{#[inline] fn inputs(&self,state:&Self::State)->::std::result::Result<::std::vec::Vec<Self::Input>,{rt}::ModelError>{{Self::{n}(self,state)}}}}",h.generics));
    }
    if codec {
        let mut methods = String::new();
        for part in ["state", "input", "output"] {
            let ty = match part {
                "state" => "State",
                "input" => "Input",
                _ => "Output",
            };
            methods.push_str(&format!("#[inline] fn encode_{part}(&self,value:&Self::{ty})->::std::result::Result<::std::vec::Vec<::std::primitive::u8>,{rt}::ModelError>{{{rt}::value_codec::TraceEncode::trace_bytes(value,::std::primitive::usize::MAX)}} #[inline] fn encode_{part}_into(&self,value:&Self::{ty},out:&mut {rt}::EncodeBuffer<'_>)->::std::result::Result<(),{rt}::ModelError>{{{rt}::value_codec::TraceEncode::trace_encode(value,out)}}"));
            if part != "output" {
                methods.push_str(&format!("#[inline] fn decode_{part}(&self,bytes:&[::std::primitive::u8])->::std::result::Result<Self::{ty},{rt}::ModelError>{{{rt}::value_codec::TraceDecode::from_trace(bytes,{decode_limits})}}"));
            }
        }
        extra.push_str(&format!(
            "impl {} {rt}::ModelCodec for {target} {where_clause} {{ {methods} }}",
            h.generics
        ));
    }
    let catalog = checks
        .iter()
        .map(|(phase, id, _)| format!("({phase:?},{id})"))
        .collect::<Vec<_>>()
        .join(",");
    extra.push_str(&format!("impl {} {target} {where_clause} {{ pub const STATELESS_PROPERTIES: &'static [(&'static ::std::primitive::str,&'static ::std::primitive::str)] = &[{catalog}]; }}",h.generics));
    let clean: TokenStream = clean.into_iter().collect();
    Ok(format!(
        "impl {} {target} {where_clause} {{ {clean} }} const _: () = {rt}::modeling::MACRO_API_V1; impl {} {rt}::Model for {target} {where_clause} {{ {methods} }} {extra}",
        h.generics, h.generics
    ))
}
