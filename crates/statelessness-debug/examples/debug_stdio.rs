//! Launch with cargo run -p statelessness-debug --example debug_stdio --offline.
//! stdin/stdout contain only length-framed protocol v1. See DEBUGGER-PROTOCOL.md.
use stateless::demo::RequestModel;
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use statelessness_debug::inspect::PathSegment;
use statelessness_debug::protocol::{
    ProbeProfile, ProtocolAuthority, ProtocolLimits, ProtocolSession, serve,
};
use statelessness_debug::session::{DebugSession, InputPolicy, SessionLimits};
use statelessness_debug::watches::{WatchAuthorization, WatchLimits, WatchRegistry};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Parse startup authority switches once, before creating any session. A
    // misspelled read-only option must never silently enable input delivery.
    let (mut fixed, mut readonly, mut probe_profiles, mut export_exact) =
        (false, false, false, false);
    for argument in std::env::args_os().skip(1) {
        match argument.to_str() {
            Some("--fixed") => fixed = true,
            Some("--read-only") => readonly = true,
            Some("--probe-profiles") => probe_profiles = true,
            Some("--export-exact") => export_exact = true,
            _ => return Err("unrecognized debugger harness option".into()),
        }
    }
    let model = if fixed {
        RequestModel::fixed()
    } else {
        RequestModel::buggy()
    };
    let session = DebugSession::recording(
        "request-lifecycle",
        model,
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )?;
    let authority = ProtocolAuthority {
        deliver_inputs: !readonly,
        configure_watches: true,
        configure_diagnostics: probe_profiles,
        read_exact_values: false,
        export_exact_trace: export_exact,
    };
    let mut endpoint = ProtocolSession::new(session, 1, authority, ProtocolLimits::default())
        .map_err(|error| format!("protocol initialization: {}", error.code()))?;
    endpoint.enable_inspection();
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_paths(vec![
                vec![PathSegment::Field("active".into())],
                vec![PathSegment::Field("ready".into())],
                vec![PathSegment::Field("generation".into())],
                vec![PathSegment::Field("pending".into())],
            ]),
        ))
        .unwrap();
    if probe_profiles {
        attach_synthetic_probes(&mut endpoint);
    }
    serve(
        &mut endpoint,
        &mut std::io::stdin().lock(),
        &mut std::io::stdout().lock(),
    )?;
    Ok(())
}

/// An explicitly separate synthetic producer, not a modeled request or effect.
/// Only this host startup acknowledges revision 1. Remote reconfiguration leaves
/// the producer pending; the protocol never invents a safe-point acknowledgement.
fn attach_synthetic_probes(endpoint: &mut ProtocolSession<RequestModel>) {
    use statelessness_debug::diagnostic::*;
    let profile = ProbeProfile {
        id: 1,
        label: "Synthetic seed".into(),
        selected: SelectedSubscription {
            subscription: Subscription {
                sink: 1,
                site: "fixture.seed".into(),
                kind: SiteKind::Probe,
                path: vec![],
                trigger: Trigger::Every,
                sample_every: 1,
                minimum_severity: Severity::Debug,
            },
            selector: ScopeSelector {
                origin: Some(DiagnosticOrigin::Test),
                ..Default::default()
            },
        },
    };
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(
        1,
        SinkPermissions {
            sites: vec!["fixture.seed".into()],
            paths: vec![vec![]],
        },
        SinkLimits::default(),
    )
    .unwrap();
    hub.register_producer(1, 1).unwrap();
    hub.configure_selected(0, vec![profile.selected.clone()])
        .unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    let mut turn = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Test).unwrap();
    turn.probe("fixture.seed", || 7u64);
    turn.finish(true);
    endpoint
        .attach_probes(
            std::rc::Rc::new(std::cell::RefCell::new(hub)),
            vec![profile],
            vec![1],
        )
        .unwrap();
}
