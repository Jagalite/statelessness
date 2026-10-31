//! Reports platform-specific Rust watch budget headers for cross-port audits.
use statelessness_debug::{
    inspect::{IntegerType, PathSegment, Scalar},
    watches::{WatchAuthorization, WatchConfig, WatchLimits, WatchRegistry, WatchTrigger},
};
fn main() {
    println!(
        "watch_config={} path_segment={}",
        std::mem::size_of::<WatchConfig>(),
        std::mem::size_of::<PathSegment>()
    );
    let mut config = WatchConfig::changed(vec![PathSegment::Field("é".into())], 1);
    config.selector.model = Some("😺".into());
    config.trigger = WatchTrigger::Equals(Scalar::Integer {
        kind: IntegerType::U128,
        decimal: format!("{}7", "0".repeat(79)),
    });
    for max in [413, 414] {
        let limits = WatchLimits {
            max_config_bytes: max,
            ..Default::default()
        };
        let mut registry = WatchRegistry::new(limits, WatchAuthorization::allow_all());
        println!(
            "max_config_bytes={max} admitted={}",
            registry.add(config.clone()).is_ok()
        );
    }
}
