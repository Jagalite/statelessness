fn main() {
    println!("cargo:rerun-if-env-changed=JOBS_BUILD");
    println!(
        "cargo:rustc-env=JOBS_BUILD={}",
        std::env::var("JOBS_BUILD").unwrap_or_else(|_| "unqualified-local-build".into())
    );
}
