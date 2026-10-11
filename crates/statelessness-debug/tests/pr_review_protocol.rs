//! Independent pre-PR regression tests against the v1.1 protocol/security contract.
use stateless::demo::RequestModel;
use statelessness_debug::effects::{
    Clock as HostClock, ClockDomain, EffectObserver, EffectOptions, LocalInstant, MeasurementError,
    RequestOrigin,
};
use statelessness_debug::protocol::*;
use statelessness_debug::session::{DebugSession, InputPolicy, SessionLimits};
use std::sync::Arc;

fn endpoint(limits: ProtocolLimits, authority: ProtocolAuthority) -> ProtocolSession<RequestModel> {
    ProtocolSession::new(
        DebugSession::new(
            "review",
            RequestModel::fixed(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
        )
        .unwrap(),
        70,
        authority,
        limits,
    )
    .unwrap()
}
fn request<M: stateless::ModelCodec + stateless::Enumerate>(
    endpoint: &ProtocolSession<M>,
    id: u64,
    command: Command,
) -> Request {
    Request {
        version: PROTOCOL_VERSION,
        session: "review".into(),
        epoch: endpoint.epoch(),
        request_id: id,
        expected_revision: endpoint.session().revision(),
        expected_configuration: endpoint.configuration_revision(),
        command,
    }
}
fn query<M: stateless::ModelCodec + stateless::Enumerate>(
    endpoint: &mut ProtocolSession<M>,
    id: u64,
    command: Command,
) -> Response {
    endpoint.handle(request(endpoint, id, command))
}
fn field<'a>(response: &'a Response, key: &str) -> &'a str {
    &response
        .fields
        .iter()
        .find(|(name, _)| name == key)
        .unwrap_or_else(|| panic!("missing {key}: {response:?}"))
        .1
}
fn number(response: &Response, key: &str) -> u64 {
    field(response, key)
        .strip_prefix("u64:")
        .unwrap()
        .parse()
        .unwrap()
}
struct Clock;
impl HostClock for Clock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        Ok(LocalInstant {
            domain: ClockDomain(1),
            nanos: 100,
        })
    }
}
fn observer() -> EffectObserver {
    EffectObserver::new(EffectOptions::default(), Arc::new(Clock)).unwrap()
}
fn origin() -> RequestOrigin {
    RequestOrigin {
        run: 1,
        epoch: 1,
        machine: 1,
        transition_sequence: 1,
        output_index: 0,
    }
}

#[test]
fn rejected_new_metric_capture_preserves_last_acknowledged_handle() {
    let mut endpoint = endpoint(
        ProtocolLimits {
            max_response_fields: 32,
            ..Default::default()
        },
        ProtocolAuthority::default(),
    );
    let observer = observer();
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let first = query(
        &mut endpoint,
        1,
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 1,
        },
    );
    assert_eq!(first.result, Ok(()));
    let handle = number(&first, "metrics.handle");
    let _effect = observer.requested(origin(), Default::default()).unwrap();
    let oversized = query(
        &mut endpoint,
        2,
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 100,
        },
    );
    assert_eq!(oversized.result, Err(ProtocolError::Limit));
    let original = query(
        &mut endpoint,
        3,
        Command::MetricSnapshot {
            handle: Some(handle),
            offset: 0,
            limit: 1,
        },
    );
    assert_eq!(
        original.result,
        Ok(()),
        "a rejected new capture must not evict the acknowledged paged snapshot"
    );
    assert_eq!(
        number(&original, "metrics.revision"),
        number(&first, "metrics.revision")
    );
    assert_eq!(number(&original, "metrics.series.total"), 0);
    assert_eq!(endpoint.session().sequence(), 0);
}

#[test]
fn rejected_metric_window_preserves_last_acknowledged_handle() {
    let mut endpoint = endpoint(
        ProtocolLimits {
            max_response_fields: 32,
            ..Default::default()
        },
        ProtocolAuthority::default(),
    );
    let observer = observer();
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let first = query(
        &mut endpoint,
        1,
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 1,
        },
    );
    assert_eq!(first.result, Ok(()));
    let handle = number(&first, "metrics.handle");
    let from = number(&first, "metrics.revision");
    let _effect = observer.requested(origin(), Default::default()).unwrap();
    let to = observer.metric_snapshot().revision;
    let rejected = query(
        &mut endpoint,
        2,
        Command::MetricWindow {
            from,
            to,
            limit: 100,
        },
    );
    assert_eq!(rejected.result, Err(ProtocolError::Limit));
    let original = query(
        &mut endpoint,
        3,
        Command::MetricSnapshot {
            handle: Some(handle),
            offset: 0,
            limit: 1,
        },
    );
    assert_eq!(original.result, Ok(()));
    assert_eq!(number(&original, "metrics.revision"), from);
}

#[test]
fn minimum_wire_limits_keep_mutation_acks_and_retry_identity() {
    use statelessness_debug::inspect::PathSegment;
    use statelessness_debug::watches::{WatchAuthorization, WatchLimits, WatchRegistry};
    let limits = ProtocolLimits {
        max_frame_bytes: 2048,
        max_field_bytes: 64,
        max_response_fields: 32,
        max_cache_bytes: 4096,
        ..Default::default()
    };
    let mut endpoint = endpoint(
        limits,
        ProtocolAuthority {
            deliver_inputs: true,
            configure_watches: true,
            ..Default::default()
        },
    );
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_paths(vec![vec![PathSegment::Field("ready".into())]]),
        ))
        .unwrap();
    let watch = query(
        &mut endpoint,
        u64::MAX - 5,
        Command::Watch {
            schema: 1,
            baseline_revision: 0,
            path: vec![PathSegment::Field("ready".into())],
        },
    );
    assert_eq!(watch.result, Ok(()));
    let watch_id = number(&watch, "watch.id");
    let removal = query(&mut endpoint, u64::MAX - 4, Command::Unwatch { watch_id });
    assert_eq!(removal.result, Ok(()));
    let input = request(
        &endpoint,
        u64::MAX - 3,
        Command::Step {
            encoded_input: vec![0],
        },
    );
    let delivered = endpoint.handle(input.clone());
    assert_eq!(delivered.result, Ok(()));
    assert_eq!(field(&delivered, "delivered"), "bool:true");
    assert_eq!(endpoint.handle(input), delivered);
    let cancelled = query(&mut endpoint, u64::MAX - 2, Command::Cancel);
    assert_eq!(cancelled.result, Ok(()));
    for response in [watch, removal, delivered, cancelled] {
        let wire = encode_response(&response, endpoint.limits()).unwrap();
        assert!(wire.len() <= 2048);
        assert_eq!(decode_response(&wire, endpoint.limits()).unwrap(), response);
    }
    assert_eq!(endpoint.session().sequence(), 1);
}

#[test]
fn evicted_candidate_and_snapshot_handles_do_not_alias_replacements() {
    let mut endpoint = endpoint(
        ProtocolLimits {
            max_candidates: 1,
            max_handles: 1,
            ..Default::default()
        },
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
    );
    let first_snapshot = query(&mut endpoint, 1, Command::Snapshot);
    let first_handle = number(&first_snapshot, "handle");
    let second_snapshot = query(&mut endpoint, 2, Command::Snapshot);
    let second_handle = number(&second_snapshot, "handle");
    assert_ne!(first_handle, second_handle);
    assert_eq!(
        query(
            &mut endpoint,
            3,
            Command::Checks {
                handle: first_handle
            }
        )
        .result,
        Err(ProtocolError::StaleHandle)
    );
    assert_eq!(
        query(
            &mut endpoint,
            4,
            Command::Checks {
                handle: second_handle
            }
        )
        .result,
        Ok(())
    );
    let first = query(
        &mut endpoint,
        5,
        Command::Inputs {
            offset: 0,
            limit: 1,
        },
    );
    let token = number(&first, "candidates.0.token");
    let second = query(
        &mut endpoint,
        6,
        Command::Inputs {
            offset: 0,
            limit: 1,
        },
    );
    let current_token = number(&second, "candidates.0.token");
    assert_ne!(token, current_token);
    assert_eq!(
        query(&mut endpoint, 7, Command::Select { token }).result,
        Err(ProtocolError::StaleHandle)
    );
    assert_eq!(endpoint.session().sequence(), 0);
    assert_eq!(
        query(
            &mut endpoint,
            8,
            Command::Select {
                token: current_token
            }
        )
        .result,
        Ok(())
    );
    assert_eq!(endpoint.session().sequence(), 1);
}

#[test]
fn discovery_is_read_only_and_reconnect_cannot_resurrect_cached_mutation() {
    let mut endpoint = endpoint(
        ProtocolLimits::default(),
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
    );
    let input = request(
        &endpoint,
        1,
        Command::Step {
            encoded_input: vec![0],
        },
    );
    let mut no_epoch = input.clone();
    no_epoch.epoch = 0;
    assert_eq!(
        endpoint.handle(no_epoch).result,
        Err(ProtocolError::WrongEpoch)
    );
    assert_eq!(endpoint.handle(input.clone()).result, Ok(()));
    endpoint.disconnect();
    endpoint.reconnect().unwrap();
    assert_eq!(
        endpoint.handle(input).result,
        Err(ProtocolError::WrongEpoch)
    );
    let mut discovery = request(&endpoint, 1, Command::Status);
    discovery.epoch = 0;
    let status = endpoint.handle(discovery);
    assert_eq!(status.result, Ok(()));
    assert_eq!(number(&status, "sequence"), 1);
    let mut stale = request(
        &endpoint,
        2,
        Command::Step {
            encoded_input: vec![0],
        },
    );
    stale.expected_revision = 0;
    assert_eq!(
        endpoint.handle(stale).result,
        Err(ProtocolError::StaleRevision)
    );
    assert_eq!(endpoint.session().sequence(), 1);
}

#[test]
fn fatal_frame_after_committed_turn_disconnects_without_rollback_or_extra_delivery() {
    use std::io::{Cursor, ErrorKind};
    let mut endpoint = endpoint(
        ProtocolLimits::default(),
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
    );
    let input = request(
        &endpoint,
        1,
        Command::Step {
            encoded_input: vec![0],
        },
    );
    let mut bytes = Vec::new();
    write_frame(
        &mut bytes,
        &encode_request(&input, endpoint.limits()).unwrap(),
        endpoint.limits().max_frame_bytes,
    )
    .unwrap();
    bytes.extend_from_slice(&u32::MAX.to_be_bytes());
    let mut output = Vec::new();
    let error = serve(&mut endpoint, &mut Cursor::new(bytes), &mut output).unwrap_err();
    assert_eq!(error.kind(), ErrorKind::InvalidData);
    let mut output = Cursor::new(output);
    let response = decode_response(
        &read_frame(&mut output, endpoint.limits().max_frame_bytes)
            .unwrap()
            .unwrap(),
        endpoint.limits(),
    )
    .unwrap();
    assert_eq!(response.result, Ok(()));
    assert!(
        read_frame(&mut output, endpoint.limits().max_frame_bytes)
            .unwrap()
            .is_none()
    );
    assert_eq!(endpoint.session().sequence(), 1);
    assert_eq!(
        query(&mut endpoint, 2, Command::Status).result,
        Err(ProtocolError::Disconnected)
    );
    endpoint.reconnect().unwrap();
    assert_eq!(
        number(&query(&mut endpoint, 1, Command::Status), "sequence"),
        1
    );
}

#[test]
fn every_truncated_prefix_and_oversize_header_closes_without_delivery() {
    use std::io::Cursor;
    for cut in 1..100 {
        let mut endpoint = endpoint(
            ProtocolLimits::default(),
            ProtocolAuthority {
                deliver_inputs: true,
                ..Default::default()
            },
        );
        let mut complete = Vec::new();
        write_frame(
            &mut complete,
            &encode_request(
                &request(
                    &endpoint,
                    1,
                    Command::Step {
                        encoded_input: vec![0],
                    },
                ),
                endpoint.limits(),
            )
            .unwrap(),
            endpoint.limits().max_frame_bytes,
        )
        .unwrap();
        if cut >= complete.len() {
            break;
        }
        let mut output = Vec::new();
        assert!(
            serve(
                &mut endpoint,
                &mut Cursor::new(&complete[..cut]),
                &mut output
            )
            .is_err()
        );
        assert!(output.is_empty());
        assert_eq!(endpoint.session().sequence(), 0);
    }
    for length in [0, 65_537, u32::MAX] {
        let mut endpoint = endpoint(
            ProtocolLimits::default(),
            ProtocolAuthority {
                deliver_inputs: true,
                ..Default::default()
            },
        );
        let mut output = Vec::new();
        assert!(
            serve(
                &mut endpoint,
                &mut Cursor::new(length.to_be_bytes()),
                &mut output
            )
            .is_err()
        );
        assert!(output.is_empty());
        assert_eq!(endpoint.session().sequence(), 0);
    }
}

#[test]
fn exposed_probe_sink_redacts_payload_and_never_drains_other_sink() {
    use statelessness_debug::diagnostic::*;
    use std::{cell::RefCell, rc::Rc};
    #[derive(statelessness_macros::Inspect)]
    struct PrivateValue {
        public: u128,
        #[inspect(redact)]
        secret: String,
    }
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    let mut selected = Vec::new();
    for sink in [1, 2] {
        hub.add_sink(sink, SinkPermissions::local_all(), SinkLimits::default())
            .unwrap();
        selected.push(SelectedSubscription {
            subscription: Subscription {
                sink,
                site: "value".into(),
                kind: SiteKind::Probe,
                path: vec![],
                trigger: Trigger::Every,
                sample_every: 1,
                minimum_severity: Severity::Debug,
            },
            selector: ScopeSelector::default(),
        });
    }
    hub.register_producer(9, 1).unwrap();
    hub.configure_selected(0, selected).unwrap();
    hub.acknowledge(9, 1, 0).unwrap();
    let private = PrivateValue {
        public: u128::MAX,
        secret: "host-secret-never-on-wire".into(),
    };
    let mut turn = hub.begin_turn(9, 1, 0, DiagnosticOrigin::Test).unwrap();
    turn.probe("value", || &private);
    turn.finish(true);
    let hub = Rc::new(RefCell::new(hub));
    let mut endpoint = endpoint(ProtocolLimits::default(), ProtocolAuthority::default());
    endpoint
        .attach_probes(hub.clone(), vec![], vec![1])
        .unwrap();
    assert_eq!(
        query(
            &mut endpoint,
            1,
            Command::ProbeEvents {
                sink: 2,
                limit: 1,
                include_payload: true
            }
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(hub.borrow().queued(2).unwrap().0, 1);
    let response = query(
        &mut endpoint,
        2,
        Command::ProbeEvents {
            sink: 1,
            limit: 1,
            include_payload: true,
        },
    );
    assert_eq!(response.result, Ok(()));
    let wire = String::from_utf8(encode_response(&response, endpoint.limits()).unwrap()).unwrap();
    assert!(!wire.contains(&private.secret));
    assert!(wire.contains("enum:redacted"));
    assert!(wire.contains(&format!("u128:{}", u128::MAX)));
    assert_eq!(hub.borrow().queued(1).unwrap().0, 0);
    assert_eq!(hub.borrow().queued(2).unwrap().0, 1);
    endpoint.disconnect();
    endpoint.reconnect().unwrap();
    assert_eq!(
        query(
            &mut endpoint,
            1,
            Command::ProbeEvents {
                sink: 2,
                limit: 1,
                include_payload: false
            }
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(hub.borrow().queued(2).unwrap().0, 1);
    assert_eq!(endpoint.session().sequence(), 0);
}

#[test]
fn exact_value_permission_cannot_export_raw_trace() {
    use stateless::monitor::RecorderOptions;
    use stateless::trace::RunConfig;
    let session = DebugSession::recording(
        "review",
        RequestModel::fixed(),
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    let mut endpoint = ProtocolSession::new(
        session,
        71,
        ProtocolAuthority {
            read_exact_values: true,
            ..Default::default()
        },
        ProtocolLimits::default(),
    )
    .unwrap();
    let snapshot = query(&mut endpoint, 1, Command::Snapshot);
    let response = query(
        &mut endpoint,
        2,
        Command::ExportTrace {
            handle: number(&snapshot, "handle"),
            offset: 0,
            limit: 100,
        },
    );
    assert_eq!(response.result, Err(ProtocolError::Unauthorized));
    assert!(response.fields.is_empty());
    assert_eq!(endpoint.session().sequence(), 0);
}

struct ManyChecks(&'static str);
impl stateless::Model for ManyChecks {
    type State = u8;
    type Input = ();
    type Output = ();
    fn metadata(&self) -> stateless::ModelMetadata {
        stateless::ModelMetadata {
            name: "many-checks".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "review".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, stateless::ModelError> {
        Ok(0)
    }
    fn step(
        &self,
        state: &u8,
        _: &(),
    ) -> Result<stateless::Transition<u8, ()>, stateless::ModelError> {
        Ok(stateless::Transition::accepted(*state + 1, vec![]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<stateless::Check>, stateless::ModelError> {
        let mut checks: Vec<_> = (0..128)
            .map(|i| stateless::Check::passed(format!("pass-{i}")))
            .collect();
        if self.0 == "initial_state" || (self.0 == "post_state" && *state > 0) {
            checks.push(stateless::Check::failed(
                "late-first-failure",
                "private-failure-message",
            ));
        }
        Ok(checks)
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &(),
        _: &stateless::TransitionRef<'_, u8, ()>,
    ) -> Result<Vec<stateless::Check>, stateless::ModelError> {
        Ok(if self.0 == "transition" {
            vec![stateless::Check::failed(
                "late-first-failure",
                "private-failure-message",
            )]
        } else {
            vec![]
        })
    }
}
impl stateless::ModelCodec for ManyChecks {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, stateless::ModelError> {
        match bytes {
            [state] => Ok(*state),
            _ => Err(stateless::ModelError::new("invalid")),
        }
    }
    fn encode_input(&self, _: &()) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<(), stateless::ModelError> {
        if bytes.is_empty() {
            Ok(())
        } else {
            Err(stateless::ModelError::new("invalid"))
        }
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![])
    }
}
impl stateless::Enumerate for ManyChecks {
    fn inputs(&self, _: &u8) -> Result<Vec<()>, stateless::ModelError> {
        Ok(vec![()])
    }
}
#[test]
fn first_failure_identity_survives_check_page_truncation_without_message_leak() {
    for phase in ["initial_state", "post_state", "transition"] {
        let session = DebugSession::new(
            "review",
            ManyChecks(phase),
            InputPolicy::enumerated(),
            SessionLimits::default(),
        )
        .unwrap();
        let mut endpoint = ProtocolSession::new(
            session,
            72,
            ProtocolAuthority {
                deliver_inputs: true,
                ..Default::default()
            },
            ProtocolLimits {
                max_page_size: 1,
                ..Default::default()
            },
        )
        .unwrap();
        if phase != "initial_state" {
            assert_eq!(
                query(
                    &mut endpoint,
                    1,
                    Command::Step {
                        encoded_input: vec![]
                    }
                )
                .result,
                Ok(())
            );
        }
        let snapshot = query(&mut endpoint, 2, Command::Snapshot);
        let response = query(
            &mut endpoint,
            3,
            Command::Checks {
                handle: number(&snapshot, "handle"),
            },
        );
        assert_eq!(response.result, Ok(()));
        assert_eq!(field(&response, "checks.truncated"), "bool:true");
        assert_eq!(
            field(&response, "checks.first_failure.id"),
            "string:late-first-failure"
        );
        assert_eq!(number(&response, "checks.first_failure.index"), 128);
        assert_eq!(
            field(&response, "checks.first_failure.phase"),
            format!("enum:{phase}")
        );
        assert_eq!(
            number(&response, "checks.first_failure.sequence"),
            u64::from(phase != "initial_state")
        );
        assert!(
            !String::from_utf8(encode_response(&response, endpoint.limits()).unwrap())
                .unwrap()
                .contains("private-failure-message")
        );
    }
}

#[test]
fn rejected_metric_captures_cannot_evict_acknowledged_window_endpoints() {
    let observer = EffectObserver::new(
        EffectOptions {
            metric_limits: statelessness_debug::metrics::MetricLimits {
                max_snapshots: 2,
                ..Default::default()
            },
            ..Default::default()
        },
        Arc::new(Clock),
    )
    .unwrap();
    let mut endpoint = endpoint(
        ProtocolLimits {
            max_response_fields: 32,
            ..Default::default()
        },
        ProtocolAuthority::default(),
    );
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let first = query(
        &mut endpoint,
        1,
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 1,
        },
    );
    let second = query(
        &mut endpoint,
        2,
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 1,
        },
    );
    assert_eq!(first.result, Ok(()));
    assert_eq!(second.result, Ok(()));
    let from = number(&first, "metrics.revision");
    let to = number(&second, "metrics.revision");
    let acknowledged = observer.available_windows();
    assert_eq!(acknowledged, vec![from, to]);
    let _effect = observer.requested(origin(), Default::default()).unwrap();
    for id in 3..7 {
        let response = query(
            &mut endpoint,
            id,
            Command::MetricSnapshot {
                handle: None,
                offset: 0,
                limit: 100,
            },
        );
        assert_eq!(response.result, Err(ProtocolError::Limit));
    }
    assert_eq!(
        observer.available_windows(),
        acknowledged,
        "rejected wire captures must not advance or evict host snapshot history"
    );
    let window = query(
        &mut endpoint,
        7,
        Command::MetricWindow { from, to, limit: 1 },
    );
    assert_eq!(window.result, Ok(()));
    assert_eq!(number(&window, "metrics.series.total"), 0);
    assert_eq!(endpoint.session().sequence(), 0);
}
