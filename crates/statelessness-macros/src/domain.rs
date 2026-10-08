use crate::parse::*;
use proc_macro::{Delimiter, TokenStream};
pub fn expand(input: TokenStream) -> Result<String> {
    let ts = tokens(input);
    let storage = fresh_ident(&ts, "__stateless_domain");
    let mut i = 0;
    let mut rt = "::stateless".to_string();
    if ts.first().is_some_and(|t| is(t, "crate")) {
        if ts.len() < 4 || !is(&ts[1], "=") || !is(&ts[3], ";") {
            return Err(error("SM300: expected crate = \"path\";"));
        }
        rt = literal(&ts[2].to_string())?;
        i = 4;
    }
    let start = i;
    if ts.get(i).is_some_and(|t| is(t, "pub")) {
        i += 1;
    }
    if !ts.get(i).is_some_and(|t| is(t, "fn")) {
        return Err(error(
            "SM301: expected [pub] fn domain(model: &Model, state: &State) -> Input",
        ));
    }
    i += 1;
    let name = ts
        .get(i)
        .ok_or_else(|| error("SM301: missing domain name"))?
        .to_string();
    i += 1;
    let params = group(
        ts.get(i)
            .ok_or_else(|| error("SM301: missing parameters"))?,
        Delimiter::Parenthesis,
    )?;
    i += 1;
    if !ts.get(i).is_some_and(|t| is(t, "-")) || !ts.get(i + 1).is_some_and(|t| is(t, ">")) {
        return Err(error("SM301: expected input return type"));
    }
    i += 2;
    let body_index = ts
        .len()
        .checked_sub(1)
        .ok_or_else(|| error("SM301: missing body"))?;
    if body_index <= i {
        return Err(error("SM301: input type and domain body required"));
    }
    let input_type = text(&ts[i..body_index]);
    let body = group(&ts[body_index], Delimiter::Brace)?;
    let mut assumption = None;
    let mut limit = None;
    let mut account = None;
    let mut statements = String::new();
    for p in split(&tokens(body.stream()), ";") {
        let kind = p[0].to_string();
        match kind.as_str() {
            "assumptions" | "limit" => {
                if p.len() < 3 || !is(&p[1], "=") {
                    return Err(error("SM302: expected assumptions/limit = value"));
                }
                let value = text(&p[2..]);
                let slot = if kind == "assumptions" {
                    literal(&value)?;
                    &mut assumption
                } else {
                    &mut limit
                };
                if slot.replace(value).is_some() {
                    return Err(error("SM302: duplicate domain descriptor"));
                }
            }
            "variants" => {
                if p.len() != 2 || account.is_some() {
                    return Err(error(
                        "SM303: expected one variants { pattern => reason, ... } block",
                    ));
                }
                let g = group(&p[1], Delimiter::Brace)?;
                let mut arms = vec![];
                for arm in split(&tokens(g.stream()), ",") {
                    let arrow = arm
                        .windows(2)
                        .position(|w| is(&w[0], "=") && is(&w[1], ">"))
                        .ok_or_else(|| error("SM303: variant needs an explicit reason"))?;
                    let reason = text(&arm[arrow + 2..]);
                    if literal(&reason)?.is_empty() {
                        return Err(error("SM303: variant reason cannot be empty"));
                    }
                    arms.push(format!(
                        "{} => {{ let _ = {reason}; }}",
                        text(&arm[..arrow])
                    ));
                }
                account = Some(format!(
                    "let _: fn(&{input_type}) = |input:&{input_type}| {{ match input {{ {} }} }};",
                    arms.join(",")
                ));
            }
            "one" => {
                let condition = p.iter().position(|t| is(t, "if"));
                let end = condition.unwrap_or(p.len());
                let value = text(&p[1..end]);
                if let Some(c) = condition {
                    statements.push_str(&format!(
                        "if {} {{ {storage}.push({value})?; }}",
                        text(&p[c + 1..])
                    ));
                } else {
                    statements.push_str(&format!("{storage}.push({value})?;"));
                }
            }
            "many" => {
                let f = p
                    .iter()
                    .position(|t| is(t, "for"))
                    .ok_or_else(|| error("SM304: many expression for binding in iterator"))?;
                let n = p
                    .iter()
                    .position(|t| is(t, "in"))
                    .ok_or_else(|| error("SM304: many expression for binding in iterator"))?;
                if n <= f + 1 {
                    return Err(error("SM304: missing iteration binding"));
                }
                statements.push_str(&format!(
                    "for {} in {} {{ {storage}.push({})?; }}",
                    text(&p[f + 1..n]),
                    text(&p[n + 1..]),
                    text(&p[1..f])
                ));
            }
            _ => return Err(error("SM305: unknown domain segment")),
        }
    }
    let assumption = assumption.ok_or_else(|| error("SM306: domain assumptions required"))?;
    let limit = limit.ok_or_else(|| error("SM306: domain limit required"))?;
    let account = account.ok_or_else(|| error("SM306: exhaustive variants accounting required"))?;
    let visibility = if is(&ts[start], "pub") { "pub" } else { "" };
    Ok(format!(
        "{visibility} fn {name}({params})->::std::result::Result<{rt}::modeling::Domain<{input_type}>,{rt}::ModelError>{{ {account} let mut {storage}={rt}::modeling::Domain::new({rt}::modeling::DomainDescriptor{{name:{name:?},assumptions:{assumption},max_entries:{limit}}}); {statements} ::std::result::Result::Ok({storage}) }}",
        params = params.stream()
    ))
}
