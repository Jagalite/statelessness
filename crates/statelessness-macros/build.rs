use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

fn collect(path: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(path).expect("read source directory") {
        let entry = entry.expect("source entry");
        if entry.file_type().expect("source type").is_dir() {
            collect(&entry.path(), files);
        } else {
            files.push(entry.path());
        }
    }
}

fn main() {
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=build.rs");
    let mut files = vec![PathBuf::from("Cargo.toml"), PathBuf::from("build.rs")];
    collect(Path::new("src"), &mut files);
    files.sort();
    let mut hash = 0xcbf29ce484222325u64;
    let mut add = |bytes: &[u8]| {
        for byte in (bytes.len() as u64).to_le_bytes().iter().chain(bytes) {
            hash = (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3);
        }
    };
    for file in files {
        add(file.to_string_lossy().as_bytes());
        add(&fs::read(file).expect("read source file"));
    }
    let rustc = env::var_os("RUSTC").expect("RUSTC");
    let version = Command::new(rustc)
        .arg("--version")
        .output()
        .expect("rustc version");
    add(&version.stdout);
    for key in [
        "TARGET",
        "PROFILE",
        "OPT_LEVEL",
        "DEBUG",
        "CARGO_ENCODED_RUSTFLAGS",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
        add(key.as_bytes());
        add(env::var(key).unwrap_or_default().as_bytes());
    }
    // A reproducibility label, not a cryptographic authenticity guarantee.
    println!("cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:{hash:016x}");
}
