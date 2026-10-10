//! First-party, dependency-free expansion. See docs/MACROS.md in the repository.
extern crate proc_macro;
mod adapter;
mod codec;
mod inspect;
mod parse;
use proc_macro::{Delimiter, Group, Ident, Literal, Punct, Spacing, TokenStream, TokenTree};
fn finish(result: parse::Result<String>) -> TokenStream {
    match result {
        Ok(code) => code
            .parse()
            .unwrap_or_else(|_| diagnostic(parse::error("SM000: expansion could not be parsed"))),
        Err(e) => diagnostic(e),
    }
}
fn diagnostic(e: parse::Error) -> TokenStream {
    let mut lit = Literal::string(&e.message);
    lit.set_span(e.span);
    [
        TokenTree::Ident(Ident::new("compile_error", e.span)),
        TokenTree::Punct(Punct::new('!', Spacing::Alone)),
        TokenTree::Group(Group::new(
            Delimiter::Parenthesis,
            TokenTree::Literal(lit).into(),
        )),
        TokenTree::Punct(Punct::new(';', Spacing::Alone)),
    ]
    .into_iter()
    .collect()
}
#[proc_macro_attribute]
pub fn model(args: TokenStream, item: TokenStream) -> TokenStream {
    finish(adapter::expand(args, item))
}
#[proc_macro_derive(TraceEncode, attributes(trace))]
pub fn encode(item: TokenStream) -> TokenStream {
    finish(codec::expand(item, false))
}
#[proc_macro_derive(TraceDecode, attributes(trace))]
pub fn decode(item: TokenStream) -> TokenStream {
    finish(codec::expand(item, true))
}
/// Generate bounded, read-only display traversal without changing replay or equality.
///
/// Type options: `#[inspect(crate = "::statelessness_debug", version = 1,
/// label = "Display name")]`. Field options: `id`, `label`, and the bare `redact`
/// flag. Variant options: `id` and `label`. Redacted fields are never accessed and
/// do not require an `Inspect` implementation. IDs default to field/variant names
/// (tuple field IDs are decimal indexes); schema versions default to one.
///
/// Generated source metadata identifies the derive invocation, not a faulty line
/// within a reducer. This derive adds no codec, equality, serialization, or
/// checkpoint behavior.
#[proc_macro_derive(Inspect, attributes(inspect))]
pub fn inspect(item: TokenStream) -> TokenStream {
    finish(inspect::expand(item))
}
mod domain;
#[proc_macro]
pub fn input_domain(input: TokenStream) -> TokenStream {
    finish(domain::expand(input))
}

/// Content-derived macro implementation identity for handwritten codec adapters.
#[proc_macro]
pub fn macro_build_id(input: TokenStream) -> TokenStream {
    if !input.is_empty() {
        return diagnostic(parse::error("SM006: macro_build_id takes no arguments"));
    }
    TokenTree::Literal(Literal::string(concat!(
        "macro-v1:",
        env!("STATELESS_BUILD_ID")
    )))
    .into()
}
