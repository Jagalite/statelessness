//! Emit explicit reference-controller build identities for cross-engine replay.
//! These pins select builds; they do not authenticate evidence or its producer.
use stateless::{
    Model,
    reference::{Controller, Mutation},
};

fn quoted(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
fn main() {
    let builds = Mutation::ALL
        .into_iter()
        .map(|mutation| {
            format!(
                "{}:{}",
                quoted(mutation.name()),
                quoted(&Controller::with_mutation(mutation).metadata().build)
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    println!(
        "{{\"report_build\":{},\"mutation_builds\":{{{}}}}}",
        quoted(&Controller::default().metadata().build),
        builds
    );
}
