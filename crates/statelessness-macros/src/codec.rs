use crate::parse::*;
use proc_macro::{Delimiter, TokenStream, TokenTree};
struct Field {
    name: String,
    ty: String,
    width: Option<u8>,
    adapter: Option<String>,
}
struct Shape {
    fields: Vec<Field>,
    kind: Delimiter,
}
fn shape(group: Option<&TokenTree>) -> Result<Shape> {
    let Some(TokenTree::Group(g)) = group else {
        return Ok(Shape {
            fields: vec![],
            kind: Delimiter::None,
        });
    };
    let named = g.delimiter() == Delimiter::Brace;
    let mut fields = vec![];
    for (index, f) in split(&tokens(g.stream()), ",").into_iter().enumerate() {
        let mut i = 0;
        let attrs = attributes(&f, &mut i)?;
        let mut width = None;
        let mut adapter = None;
        let mut seen = std::collections::BTreeSet::new();
        for (a, v) in attrs {
            if ["doc", "cfg", "cfg_attr", "inspect"].contains(&a.as_str()) {
                continue;
            }
            if a != "trace" {
                return Err(error(
                    "SM110: field attributes other than trace are unsupported",
                ));
            }
            for (k, v) in options(&v)? {
                if !seen.insert(k.clone()) {
                    return Err(error("SM115: duplicate trace field option"));
                }
                match k.as_str() {
                    "length" => {
                        width = Some(match literal(&v)?.as_str() {
                            "u8" => 1,
                            "u16" => 2,
                            "u32" => 4,
                            _ => return Err(error("SM111: length must be u8, u16, or u32")),
                        });
                    }
                    "with" => adapter = Some(literal(&v)?),
                    _ => return Err(error("SM112: unknown trace field option")),
                }
            }
        }
        if width.is_some() && adapter.is_some() {
            return Err(error("SM113: length and with are mutually exclusive"));
        }
        if f.get(i).is_some_and(|t| is(t, "pub")) {
            i += 1;
            if matches!(f.get(i), Some(TokenTree::Group(_))) {
                i += 1;
            }
        }
        let name = if named {
            let name = f
                .get(i)
                .ok_or_else(|| error("SM114: missing field name"))?
                .to_string();
            i += 1;
            if !f.get(i).is_some_and(|t| is(t, ":")) {
                return Err(error("SM114: expected named field"));
            }
            i += 1;
            name
        } else {
            index.to_string()
        };
        let ty = text(&f[i..]);
        if ty.is_empty() {
            return Err(error("SM114: missing field type"));
        }
        fields.push(Field {
            name,
            ty,
            width,
            adapter,
        });
    }
    Ok(Shape {
        fields,
        kind: g.delimiter(),
    })
}
fn encode(f: &Field, access: &str, rt: &str) -> String {
    if let Some(a) = &f.adapter {
        format!("{a}::encode({access}, out)?;")
    } else if let Some(w) = f.width {
        format!("{rt}::value_codec::LengthEncode::encode_length({access},{w},out)?;")
    } else {
        format!("{rt}::value_codec::TraceEncode::trace_encode({access},out)?;")
    }
}
fn decode(f: &Field, rt: &str) -> String {
    if let Some(a) = &f.adapter {
        format!("{a}::decode(reader)?")
    } else if let Some(w) = f.width {
        format!(
            "<{ty} as {rt}::value_codec::LengthDecode>::decode_length({w},reader)?",
            ty = f.ty
        )
    } else {
        format!("reader.value::<{}>()?", f.ty)
    }
}
fn construct(name: &str, s: &Shape, values: Vec<String>) -> String {
    match s.kind {
        Delimiter::Brace => format!(
            "{name} {{ {} }}",
            s.fields
                .iter()
                .zip(values)
                .map(|(f, v)| format!("{}: {v}", f.name))
                .collect::<Vec<_>>()
                .join(",")
        ),
        Delimiter::Parenthesis => format!("{name}({})", values.join(",")),
        _ => name.to_string(),
    }
}
pub fn expand(input: TokenStream, decoding: bool) -> Result<String> {
    let schema = input.to_string();
    let ts = tokens(input);
    let mut p = 0;
    let attrs = attributes(&ts, &mut p)?;
    let mut rt = "::stateless".to_string();
    let mut tag_type = "u8".to_string();
    let mut type_options = std::collections::BTreeSet::new();
    for (a, v) in attrs {
        if a == "trace" {
            for (k, v) in options(&v)? {
                if !type_options.insert(k.clone()) {
                    return Err(error("SM100: duplicate trace type option"));
                }
                match k.as_str() {
                    "crate" => rt = literal(&v)?,
                    "tag_type" => tag_type = literal(&v)?,
                    _ => return Err(error("SM100: unknown trace type option")),
                }
            }
        }
    }
    if !["u8", "u16", "u32"].contains(&tag_type.as_str()) {
        return Err(error("SM102: tag_type must be u8, u16, or u32"));
    }
    if ts.get(p).is_some_and(|t| is(t, "pub")) {
        p += 1;
        if matches!(ts.get(p), Some(TokenTree::Group(_))) {
            p += 1;
        }
    }
    let kind = ts
        .get(p)
        .ok_or_else(|| error("SM103: expected struct or enum"))?
        .to_string();
    p += 1;
    if kind != "enum" && type_options.contains("tag_type") {
        return Err(error("SM102: tag_type requires an enum"));
    }
    let name = ts
        .get(p)
        .ok_or_else(|| error("SM103: missing type name"))?
        .to_string();
    p += 1;
    let h = header(&ts[p..])?;
    let rest = &h.tail;
    let body_pos = if rest
        .first()
        .is_some_and(|t| matches!(t,TokenTree::Group(g) if g.delimiter()==Delimiter::Parenthesis))
    {
        Some(0)
    } else {
        rest.iter()
            .rposition(|t| matches!(t,TokenTree::Group(g) if g.delimiter()==Delimiter::Brace))
    };
    let body = body_pos.map(|i| &rest[i]);
    let mut where_tokens = rest.to_vec();
    if let Some(i) = body_pos {
        where_tokens.remove(i);
    }
    where_tokens.retain(|t| !is(t, ";"));
    let existing = text(&where_tokens);
    let mut bounds = vec![];
    let mut shapes = vec![];
    let mut labels = vec![];
    let code;
    if kind == "struct" {
        let s = shape(body)?;
        code = if decoding {
            format!(
                "::std::result::Result::Ok({})",
                construct(
                    "Self",
                    &s,
                    s.fields.iter().map(|f| decode(f, &rt)).collect()
                )
            )
        } else {
            format!(
                "{} ::std::result::Result::Ok(())",
                s.fields
                    .iter()
                    .map(|f| encode(f, &format!("&self.{}", f.name), &rt))
                    .collect::<String>()
            )
        };
        shapes.push(s);
    } else if kind == "enum" {
        let g = group(
            body.ok_or_else(|| error("SM104: enum body required"))?,
            Delimiter::Brace,
        )?;
        let mut arms = vec![];
        let mut tags = std::collections::BTreeSet::new();
        for v in split(&tokens(g.stream()), ",") {
            let mut i = 0;
            let attrs = attributes(&v, &mut i)?;
            let mut tag = None;
            for (a, v) in attrs {
                if ["doc", "cfg", "cfg_attr", "inspect"].contains(&a.as_str()) {
                    continue;
                }
                if a != "trace" {
                    return Err(error("SM105: variant cfg/other attributes unsupported"));
                }
                for (k, v) in options(&v)? {
                    if k != "tag" || tag.is_some() {
                        return Err(error("SM106: expected one explicit tag"));
                    }
                    tag = Some(
                        v.parse::<u32>()
                            .map_err(|_| error("SM106: tag must be a decimal integer"))?,
                    );
                }
            }
            let tag = tag.ok_or_else(|| error("SM106: every variant needs #[trace(tag = N)]"))?;
            let maximum = match tag_type.as_str() {
                "u8" => u8::MAX as u32,
                "u16" => u16::MAX as u32,
                _ => u32::MAX,
            };
            if tag > maximum || !tags.insert(tag) {
                return Err(error_at(&v[0], "SM107: duplicate or out-of-range enum tag"));
            }
            let n = v
                .get(i)
                .ok_or_else(|| error("SM108: missing variant name"))?
                .to_string();
            i += 1;
            if v.get(i).is_some_and(|t| is(t, "=")) {
                return Err(error(
                    "SM109: Rust discriminants unsupported; use trace tags",
                ));
            }
            let s = shape(v.get(i))?;
            let full = format!("Self::{n}");
            let label_pattern = match s.kind {
                Delimiter::Brace => format!("{full} {{ .. }}"),
                Delimiter::Parenthesis => format!("{full}(..)"),
                _ => full.clone(),
            };
            labels.push(format!(
                "{label_pattern} => ::std::option::Option::Some({n:?})"
            ));
            if decoding {
                arms.push(format!(
                    "{tag} => ::std::result::Result::Ok({})",
                    construct(&full, &s, s.fields.iter().map(|f| decode(f, &rt)).collect())
                ));
            } else {
                let vars = (0..s.fields.len())
                    .map(|i| format!("__field_{i}"))
                    .collect::<Vec<_>>();
                let pat = construct(&full, &s, vars.clone());
                let enc = s
                    .fields
                    .iter()
                    .zip(vars)
                    .map(|(f, v)| encode(f, &v, &rt))
                    .collect::<String>();
                arms.push(format!("{pat} => {{ {rt}::value_codec::TraceEncode::trace_encode(&({tag} as ::std::primitive::{tag_type}),out)?; {enc} ::std::result::Result::Ok(()) }}"));
            }
            shapes.push(s);
        }
        code = if decoding {
            format!(
                "match reader.value::<::std::primitive::{tag_type}>()? {{ {}, _=>::std::result::Result::Err({rt}::ModelError::new(\"unknown enum tag\")) }}",
                arms.join(",")
            )
        } else {
            format!("match self {{ {} }}", arms.join(","))
        };
    } else {
        return Err(error("SM103: only structs and enums support trace derives"));
    }
    for s in &shapes {
        for f in &s.fields {
            if f.adapter.is_none() {
                let tr = match (decoding, f.width.is_some()) {
                    (true, true) => "LengthDecode",
                    (false, true) => "LengthEncode",
                    (true, false) => "TraceDecode",
                    _ => "TraceEncode",
                };
                bounds.push(format!("{}: {rt}::value_codec::{tr}", f.ty));
            }
        }
    }
    let where_clause = if existing.is_empty() {
        if bounds.is_empty() {
            String::new()
        } else {
            format!("where {}", bounds.join(","))
        }
    } else {
        format!(
            "{} {} {}",
            existing,
            if existing.trim_end().ends_with(',') {
                ""
            } else {
                ","
            },
            bounds.join(",")
        )
    };
    let (tr, method) = if decoding {
        (
            "TraceDecode",
            format!(
                "#[inline] fn trace_decode(reader:&mut {rt}::value_codec::Decoder<'_>)->::std::result::Result<Self,{rt}::ModelError> {{ {code} }}"
            ),
        )
    } else {
        (
            "TraceEncode",
            format!(
                "#[inline] fn trace_encode(&self,out:&mut {rt}::EncodeBuffer<'_>)->::std::result::Result<(),{rt}::ModelError> {{ {code} }}"
            ),
        )
    };
    let diagnostics = if decoding {
        String::new()
    } else {
        format!(
            "fn trace_schema()-> &'static ::std::primitive::str {{ {schema:?} }} {}",
            if kind == "enum" {
                format!(
                    "fn trace_variant(&self)->::std::option::Option<&'static ::std::primitive::str>{{match self {{ {} }} }}",
                    labels.join(",")
                )
            } else {
                String::new()
            }
        )
    };
    Ok(format!(
        "const _: () = {rt}::modeling::MACRO_API_V1; impl {} {rt}::value_codec::{tr} for {name}{} {where_clause} {{ {method} {diagnostics} }}",
        h.generics, h.args
    ))
}
