use proc_macro::{Delimiter, Group, Span, TokenStream, TokenTree};
pub type Result<T> = std::result::Result<T, Error>;
pub struct Error {
    pub message: String,
    pub span: Span,
}
pub fn error(message: impl Into<String>) -> Error {
    Error {
        message: message.into(),
        span: Span::call_site(),
    }
}
pub fn error_at(token: &TokenTree, message: impl Into<String>) -> Error {
    Error {
        message: message.into(),
        span: token.span(),
    }
}
pub fn text(tokens: &[TokenTree]) -> String {
    tokens.iter().cloned().collect::<TokenStream>().to_string()
}
pub fn tokens(s: TokenStream) -> Vec<TokenTree> {
    s.into_iter().collect()
}
pub fn is(t: &TokenTree, s: &str) -> bool {
    t.to_string() == s
}
pub fn split(ts: &[TokenTree], separator: &str) -> Vec<Vec<TokenTree>> {
    let mut parts = vec![];
    let mut start = 0;
    let mut depth = 0i32;
    for (i, t) in ts.iter().enumerate() {
        if separator == "," && is(t, "<") {
            depth += 1;
        } else if separator == ","
            && is(t, ">")
            && depth > 0
            && !i.checked_sub(1).is_some_and(|p| is(&ts[p], "-"))
        {
            depth -= 1;
        }
        if depth == 0 && is(t, separator) {
            if i > start {
                parts.push(ts[start..i].to_vec());
            }
            start = i + 1;
        }
    }
    if start < ts.len() {
        parts.push(ts[start..].to_vec());
    }
    parts
}
pub fn options(ts: &[TokenTree]) -> Result<Vec<(String, String)>> {
    split(ts, ",")
        .into_iter()
        .map(|p| {
            if p.len() == 1 {
                return Ok((text(&p), String::new()));
            }
            if p.len() < 3 || !is(&p[1], "=") {
                return Err(error("SM001: expected key = value"));
            }
            Ok((p[0].to_string(), text(&p[2..])))
        })
        .collect()
}
pub fn literal(s: &str) -> Result<String> {
    if s.starts_with('"') && s.ends_with('"') && !s[1..s.len() - 1].contains('\\') {
        Ok(s[1..s.len() - 1].to_string())
    } else {
        Err(error("SM002: expected an unescaped string literal"))
    }
}
pub fn attributes(ts: &[TokenTree], pos: &mut usize) -> Result<Vec<(String, Vec<TokenTree>)>> {
    let mut attrs = vec![];
    while *pos < ts.len() && is(&ts[*pos], "#") {
        let Some(TokenTree::Group(g)) = ts.get(*pos + 1) else {
            return Err(error("SM003: malformed attribute"));
        };
        let a = tokens(g.stream());
        let Some(name) = a.first() else {
            return Err(error("SM003: empty attribute"));
        };
        let args = if a.len() == 2 {
            if let TokenTree::Group(g) = &a[1] {
                tokens(g.stream())
            } else {
                a[1..].to_vec()
            }
        } else {
            a[1..].to_vec()
        };
        attrs.push((name.to_string(), args));
        *pos += 2;
    }
    Ok(attrs)
}
pub fn group(t: &TokenTree, delimiter: Delimiter) -> Result<Group> {
    if let TokenTree::Group(g) = t
        && g.delimiter() == delimiter
    {
        return Ok(g.clone());
    }
    Err(error("SM004: expected grouped declaration"))
}

pub struct Header {
    pub generics: String,
    pub args: String,
    pub tail: Vec<TokenTree>,
}
pub fn header(ts: &[TokenTree]) -> Result<Header> {
    if ts.first().is_none_or(|t| !is(t, "<")) {
        return Ok(Header {
            generics: String::new(),
            args: String::new(),
            tail: ts.to_vec(),
        });
    }
    let mut depth = 0;
    let mut end = None;
    for (i, t) in ts.iter().enumerate() {
        if is(t, "<") {
            depth += 1;
        }
        if is(t, ">") && !i.checked_sub(1).is_some_and(|p| is(&ts[p], "-")) {
            depth -= 1;
            if depth == 0 {
                end = Some(i);
                break;
            }
        }
    }
    let end = end.ok_or_else(|| error("SM005: unclosed generic parameters"))?;
    let params = split(&ts[1..end], ",");
    let mut declarations = vec![];
    let mut args = vec![];
    for mut p in params {
        let mut nested = 0usize;
        let mut default = None;
        for (index, token) in p.iter().enumerate() {
            if is(token, "<") {
                nested += 1;
            }
            if is(token, ">") && !index.checked_sub(1).is_some_and(|i| is(&p[i], "-")) {
                nested = nested.saturating_sub(1);
            }
            if is(token, "=") && nested == 0 {
                default = Some(index);
                break;
            }
        }
        if let Some(index) = default {
            p.truncate(index);
        }
        let n = if p.first().is_some_and(|t| is(t, "const")) {
            p.get(1).map(ToString::to_string)
        } else if p.first().is_some_and(|t| is(t, "'")) {
            Some(text(&p[..2]))
        } else {
            p.first().map(ToString::to_string)
        };
        args.push(n.ok_or_else(|| error("SM005: empty generic parameter"))?);
        declarations.push(text(&p));
    }
    Ok(Header {
        generics: format!("<{}>", declarations.join(",")),
        args: format!("<{}>", args.join(",")),
        tail: ts[end + 1..].to_vec(),
    })
}

/// Pick a generated local that cannot shadow any caller token, including nested
/// iterator bindings and expressions. Do not rename caller tokens.
pub fn fresh_ident(ts: &[TokenTree], base: &str) -> String {
    fn collect(ts: &[TokenTree], names: &mut std::collections::BTreeSet<String>) {
        for token in ts {
            match token {
                TokenTree::Ident(id) => {
                    names.insert(id.to_string());
                }
                TokenTree::Group(group) => collect(&tokens(group.stream()), names),
                _ => {}
            }
        }
    }
    let mut names = std::collections::BTreeSet::new();
    collect(ts, &mut names);
    let mut name = base.to_owned();
    while names.contains(&name) {
        name.push('_');
    }
    name
}
