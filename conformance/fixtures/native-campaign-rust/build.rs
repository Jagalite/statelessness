use std::{env, fs, path::PathBuf};
fn main() {
    let manifest = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let root = manifest.join("../../..");
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    fs::create_dir_all(out.join("campaign/lifecycle")).unwrap();
    for relative in [
        "campaign.rs",
        "campaign/lifecycle.rs",
        "campaign/lifecycle/codec.rs",
        "campaign/lifecycle/tests.rs",
    ] {
        let source = root.join("src").join(relative);
        println!("cargo:rerun-if-changed={}", source.display());
        let destination = if relative == "campaign.rs" { out.join("campaign/mod.rs") } else { out.join(relative) };
        fs::write(destination, fs::read(source).unwrap()).unwrap();
    }
    let adapter = manifest.join("adapter.rs");
    println!("cargo:rerun-if-changed={}", adapter.display());
    let mut campaign = fs::read_to_string(out.join("campaign/mod.rs")).unwrap();
    campaign.push_str(&format!(
        "\n#[cfg(test)]\n#[path = {:?}]\nmod n06_reference;\n",
        adapter
    ));
    fs::write(out.join("campaign/mod.rs"), campaign).unwrap();
    fs::write(
        out.join("entry.rs"),
        format!("#[path = {:?}]\nmod campaign;\n", out.join("campaign/mod.rs")),
    )
    .unwrap();
    // Fixture metadata is deliberately distinct from the product library build.
    // Exact cryptographic input identities are in the external manifest.
    println!("cargo:rustc-env=STATELESS_BUILD_ID=n06-private-source-adapter-v1");
}
