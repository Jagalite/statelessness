//! Dependency-free, display-only inspection traversal.
use crate::parse::*;
use proc_macro::{Delimiter, TokenStream, TokenTree};
use std::collections::BTreeSet;

#[derive(Default)]
struct Metadata {
    runtime: Option<String>,
    version: Option<u32>,
    id: Option<String>,
    label: Option<String>,
    redact: bool,
}

#[derive(Clone, Copy)]
enum Scope {
    Type,
    Field,
    Variant,
}

/// Read only our attributes; other derives and ordinary Rust attributes coexist.
fn metadata(ts: &[TokenTree], pos: &mut usize, scope: Scope) -> Result<Metadata> {
    let mut result = Metadata::default();
    let mut seen = BTreeSet::new();
    while ts.get(*pos).is_some_and(|t| is(t, "#")) {
        let Some(TokenTree::Group(attribute)) = ts.get(*pos + 1) else {
            return Err(error("SM400: malformed inspect attribute"));
        };
        if attribute.delimiter() != Delimiter::Bracket {
            return Err(error("SM400: malformed inspect attribute"));
        }
        let contents = tokens(attribute.stream());
        *pos += 2;
        if !contents.first().is_some_and(|t| is(t, "inspect")) {
            continue;
        }
        let [_, TokenTree::Group(arguments)] = contents.as_slice() else {
            return Err(error("SM400: expected #[inspect(...)]"));
        };
        if arguments.delimiter() != Delimiter::Parenthesis {
            return Err(error("SM400: expected #[inspect(...)]"));
        }
        let args = tokens(arguments.stream());
        if args.first().is_some_and(|t| is(t, ","))
            || args
                .windows(2)
                .any(|pair| is(&pair[0], ",") && is(&pair[1], ","))
        {
            return Err(error("SM400: empty inspect option"));
        }
        for option in split(&args, ",") {
            let Some(TokenTree::Ident(key)) = option.first() else {
                return Err(error("SM400: expected an inspect option name"));
            };
            let key = key.to_string();
            if !seen.insert(key.clone()) {
                return Err(error_at(
                    &option[0],
                    format!("SM402: duplicate inspect option `{key}`"),
                ));
            }
            let allowed = match scope {
                Scope::Type => matches!(key.as_str(), "crate" | "version" | "label"),
                Scope::Field => matches!(key.as_str(), "id" | "label" | "redact"),
                Scope::Variant => matches!(key.as_str(), "id" | "label"),
            };
            if !allowed {
                return Err(error_at(
                    &option[0],
                    format!("SM403: unknown inspect option `{key}` for this declaration"),
                ));
            }
            if key == "redact" {
                if option.len() != 1 {
                    return Err(error_at(
                        &option[0],
                        "SM400: redact is a bare flag; use #[inspect(redact)]",
                    ));
                }
                result.redact = true;
                continue;
            }
            let [_, equals, TokenTree::Literal(value)] = option.as_slice() else {
                return Err(error_at(
                    &option[0],
                    "SM400: expected inspect option = literal",
                ));
            };
            if !is(equals, "=") {
                return Err(error_at(equals, "SM400: expected inspect option = literal"));
            }
            if key == "version" {
                result.version = Some(value.to_string().parse::<u32>().map_err(|_| {
                    error_at(
                        &option[2],
                        "SM404: inspect version must be an unsuffixed decimal u32 integer",
                    )
                })?);
                continue;
            }
            let value = string_literal(&value.to_string())?;
            match key.as_str() {
                "crate" => {
                    runtime_path(&value)?;
                    result.runtime = Some(value);
                }
                "id" => {
                    if value.is_empty() {
                        return Err(error_at(&option[2], "SM405: inspect IDs must not be empty"));
                    }
                    result.id = Some(value);
                }
                "label" => result.label = Some(value),
                _ => unreachable!(),
            }
        }
    }
    Ok(result)
}

/// Decode a Rust string so differently escaped spellings still collide as IDs.
fn string_literal(source: &str) -> Result<String> {
    let invalid = || error("SM401: expected a Rust string literal in inspect metadata");
    if let Some(raw) = source.strip_prefix('r') {
        let hashes = raw.bytes().take_while(|b| *b == b'#').count();
        let prefix = format!("{}\"", "#".repeat(hashes));
        let suffix = format!("\"{}", "#".repeat(hashes));
        return raw
            .strip_prefix(&prefix)
            .and_then(|s| s.strip_suffix(&suffix))
            .map(str::to_owned)
            .ok_or_else(invalid);
    }
    let contents = source
        .strip_prefix('"')
        .and_then(|s| s.strip_suffix('"'))
        .ok_or_else(invalid)?;
    let mut chars = contents.chars().peekable();
    let mut decoded = String::new();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            decoded.push(ch);
            continue;
        }
        match chars.next().ok_or_else(invalid)? {
            '\\' => decoded.push('\\'),
            '"' => decoded.push('"'),
            '\'' => decoded.push('\''),
            'n' => decoded.push('\n'),
            'r' => decoded.push('\r'),
            't' => decoded.push('\t'),
            '0' => decoded.push('\0'),
            'x' => {
                let a = chars
                    .next()
                    .and_then(|c| c.to_digit(16))
                    .ok_or_else(invalid)?;
                let b = chars
                    .next()
                    .and_then(|c| c.to_digit(16))
                    .ok_or_else(invalid)?;
                let value = a * 16 + b;
                if value > 0x7f {
                    return Err(invalid());
                }
                decoded.push(char::from_u32(value).ok_or_else(invalid)?);
            }
            'u' => {
                if chars.next() != Some('{') {
                    return Err(invalid());
                }
                let mut hex = String::new();
                let mut closed = false;
                for c in chars.by_ref() {
                    if c == '}' {
                        closed = true;
                        break;
                    }
                    if c != '_' {
                        hex.push(c);
                    }
                }
                if !closed || hex.is_empty() || hex.len() > 6 {
                    return Err(invalid());
                }
                decoded.push(
                    u32::from_str_radix(&hex, 16)
                        .ok()
                        .and_then(char::from_u32)
                        .ok_or_else(invalid)?,
                );
            }
            '\n' => {
                while chars.peek().is_some_and(|c| c.is_ascii_whitespace()) {
                    chars.next();
                }
            }
            _ => return Err(invalid()),
        }
    }
    Ok(decoded)
}

fn runtime_path(path: &str) -> Result<()> {
    let invalid = || error("SM406: inspect crate must name a Rust module path");
    let ts = tokens(path.parse::<TokenStream>().map_err(|_| invalid())?);
    let mut pos = 0;
    if ts.get(pos).is_some_and(|t| is(t, ":")) {
        if !ts.get(pos + 1).is_some_and(|t| is(t, ":")) {
            return Err(invalid());
        }
        pos += 2;
    }
    loop {
        if !matches!(ts.get(pos), Some(TokenTree::Ident(_))) {
            return Err(invalid());
        }
        pos += 1;
        if pos == ts.len() {
            return Ok(());
        }
        if !ts.get(pos).is_some_and(|t| is(t, ":")) || !ts.get(pos + 1).is_some_and(|t| is(t, ":"))
        {
            return Err(invalid());
        }
        pos += 2;
    }
}

struct Field {
    name: String,
    ty: String,
    id: String,
    label: String,
    redact: bool,
}

struct Shape {
    fields: Vec<Field>,
    kind: Delimiter,
}

fn skip_visibility(ts: &[TokenTree], pos: &mut usize) {
    if ts.get(*pos).is_some_and(|t| is(t, "pub")) {
        *pos += 1;
        if matches!(ts.get(*pos), Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis)
        {
            *pos += 1;
        }
    }
}

fn plain_name(name: &str) -> &str {
    name.strip_prefix("r#").unwrap_or(name)
}

fn shape(body: Option<&TokenTree>) -> Result<Shape> {
    let Some(body) = body else {
        return Ok(Shape {
            fields: Vec::new(),
            kind: Delimiter::None,
        });
    };
    let TokenTree::Group(body) = body else {
        return Err(error_at(body, "SM407: expected inspect fields"));
    };
    let named = body.delimiter() == Delimiter::Brace;
    if !named && body.delimiter() != Delimiter::Parenthesis {
        return Err(error("SM407: expected named or tuple inspect fields"));
    }
    let mut fields = Vec::new();
    let mut ids = BTreeSet::new();
    for (index, field) in split(&tokens(body.stream()), ",").into_iter().enumerate() {
        let mut pos = 0;
        let metadata = metadata(&field, &mut pos, Scope::Field)?;
        skip_visibility(&field, &mut pos);
        let name = if named {
            let Some(TokenTree::Ident(name)) = field.get(pos) else {
                return Err(error("SM407: expected named inspect field"));
            };
            pos += 1;
            if !field.get(pos).is_some_and(|t| is(t, ":")) {
                return Err(error("SM407: expected a colon after inspect field name"));
            }
            pos += 1;
            name.to_string()
        } else {
            index.to_string()
        };
        let ty = text(&field[pos..]);
        if ty.is_empty() {
            return Err(error("SM407: missing inspect field type"));
        }
        let id = metadata.id.unwrap_or_else(|| plain_name(&name).to_owned());
        if !ids.insert(id.clone()) {
            return Err(error(format!("SM408: duplicate inspect field ID {id:?}")));
        }
        fields.push(Field {
            label: metadata
                .label
                .unwrap_or_else(|| plain_name(&name).to_owned()),
            name,
            ty,
            id,
            redact: metadata.redact,
        });
    }
    Ok(Shape {
        fields,
        kind: body.delimiter(),
    })
}

fn field_view(field: &Field, access: &str, rt: &str, source: &str) -> String {
    let id = &field.id;
    let label = &field.label;
    if field.redact {
        format!("{rt}::inspect::FieldView::redacted({id:?},{label:?}).with_source({source})")
    } else {
        // The extra borrow also supports unsized tails through Inspect for &T.
        format!("{rt}::inspect::FieldView::new({id:?},{label:?},&{access}).with_source({source})")
    }
}

pub fn expand(input: TokenStream) -> Result<String> {
    let ts = tokens(input);
    let mut pos = 0;
    let meta = metadata(&ts, &mut pos, Scope::Type)?;
    let rt = meta.runtime.as_deref().unwrap_or("::statelessness_debug");
    skip_visibility(&ts, &mut pos);
    let kind = ts.get(pos).map(ToString::to_string).unwrap_or_default();
    if kind != "struct" && kind != "enum" {
        return Err(error(
            "SM409: Inspect supports structs and enums; unions are unsupported",
        ));
    }
    pos += 1;
    let Some(TokenTree::Ident(name)) = ts.get(pos) else {
        return Err(error("SM409: missing inspect type name"));
    };
    let name = name.to_string();
    pos += 1;
    let display_name = meta.label.as_deref().unwrap_or(plain_name(&name));
    // Default schema identities must distinguish same-named types in different
    // modules and their concrete generic arguments. Explicit labels remain an
    // application-owned stable schema name.
    let schema_name = meta.label.as_ref().map_or_else(
        || "::core::any::type_name::<Self>()".to_owned(),
        |label| format!("{label:?}"),
    );
    let version = meta.version.unwrap_or(1);
    let header = header(&ts[pos..])?;
    let rest = &header.tail;
    let body_pos = if matches!(rest.first(), Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis)
    {
        Some(0)
    } else {
        rest.iter()
            .rposition(|t| matches!(t, TokenTree::Group(g) if g.delimiter() == Delimiter::Brace))
    };
    let body = body_pos.map(|i| &rest[i]);
    let mut where_tokens = rest.to_vec();
    if let Some(index) = body_pos {
        where_tokens.remove(index);
    }
    where_tokens.retain(|t| !is(t, ";"));
    let existing_where = text(&where_tokens);
    let path = fresh_ident(&ts, "__inspect_path");
    let cx = fresh_ident(&ts, "__inspect_cx");
    let source = format!(
        "{rt}::inspect::SourceSite {{ file: ::core::file!(), line: ::core::line!(), column: ::core::column!() }}"
    );
    let mut shapes = Vec::new();
    let code = if kind == "struct" {
        let shape = shape(body)?;
        let fields = shape
            .fields
            .iter()
            .map(|f| field_view(f, &format!("&self.{}", f.name), rt, &source))
            .collect::<Vec<_>>()
            .join(",");
        shapes.push(shape);
        format!("{cx}.object({path},{display_name:?},&[{fields}])")
    } else {
        let body = group(
            body.ok_or_else(|| error("SM409: missing inspect enum body"))?,
            Delimiter::Brace,
        )?;
        let mut arms = Vec::new();
        let mut ids = BTreeSet::new();
        for (variant_index, variant) in split(&tokens(body.stream()), ",").into_iter().enumerate() {
            let mut pos = 0;
            let meta = metadata(&variant, &mut pos, Scope::Variant)?;
            let Some(TokenTree::Ident(variant_name)) = variant.get(pos) else {
                return Err(error("SM410: missing inspect variant name"));
            };
            let variant_name = variant_name.to_string();
            pos += 1;
            if variant[pos..].iter().any(|t| is(t, "=")) {
                return Err(error(
                    "SM411: Rust discriminants are unsupported by Inspect; use inspect variant IDs",
                ));
            }
            if variant.len() > pos + 1 {
                return Err(error("SM410: malformed inspect variant"));
            }
            let shape = shape(variant.get(pos))?;
            let id = meta
                .id
                .unwrap_or_else(|| plain_name(&variant_name).to_owned());
            let label = meta
                .label
                .unwrap_or_else(|| plain_name(&variant_name).to_owned());
            if !ids.insert(id.clone()) {
                return Err(error(format!("SM412: duplicate inspect variant ID {id:?}")));
            }
            let variables = shape
                .fields
                .iter()
                .enumerate()
                .map(|(index, f)| {
                    if f.redact {
                        "_".to_owned()
                    } else {
                        fresh_ident(&ts, &format!("__inspect_field_{variant_index}_{index}"))
                    }
                })
                .collect::<Vec<_>>();
            let pattern = match shape.kind {
                Delimiter::Brace => format!(
                    "Self::{variant_name} {{ {} }}",
                    shape
                        .fields
                        .iter()
                        .zip(&variables)
                        .map(|(field, var)| format!("{}: {var}", field.name))
                        .collect::<Vec<_>>()
                        .join(",")
                ),
                Delimiter::Parenthesis => format!("Self::{variant_name}({})", variables.join(",")),
                _ => format!("Self::{variant_name}"),
            };
            let fields = shape
                .fields
                .iter()
                .zip(&variables)
                .map(|(field, var)| field_view(field, var, rt, &source))
                .collect::<Vec<_>>()
                .join(",");
            arms.push(format!("{pattern} => {cx}.enumeration({path},{display_name:?},{id:?},{label:?},&[{fields}])"));
            shapes.push(shape);
        }
        if arms.is_empty() {
            // Matching a reference to an uninhabited enum is not exhaustive;
            // matching its place value is exhaustive and does not move a value.
            "match *self {}".to_owned()
        } else {
            format!("match self {{ {} }}", arms.join(","))
        }
    };
    let bounds = shapes
        .iter()
        .flat_map(|s| &s.fields)
        .filter(|f| !f.redact)
        .map(|f| format!("{}: {rt}::inspect::Inspect", f.ty))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>()
        .join(",");
    let where_clause = match (existing_where.is_empty(), bounds.is_empty()) {
        (true, true) => String::new(),
        (true, false) => format!("where {bounds}"),
        (false, true) => existing_where,
        (false, false) => format!(
            "{} {} {bounds}",
            existing_where,
            if existing_where.trim_end().ends_with(',') {
                ""
            } else {
                ","
            }
        ),
    };
    Ok(format!(
        "impl {generics} {rt}::inspect::Inspect for {name}{args} {where_clause} {{
            fn inspect(&self,{path}:&[{rt}::inspect::PathSegment],{cx}:&mut {rt}::inspect::InspectContext)->::core::result::Result<{rt}::inspect::InspectNode,{rt}::inspect::InspectError> {{ {code} }}
            fn schema(&self)->{rt}::inspect::DisplaySchema {{ {rt}::inspect::DisplaySchema {{ name: {schema_name}, version: {version}, source: ::core::option::Option::Some({source}) }} }}
        }}",
        generics = header.generics,
        args = header.args,
    ))
}
