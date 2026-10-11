use stateless::ModelCodec;
use stateless::demo::{Input, RequestModel};
use statelessness_debug::inspect::PathSegment;
use statelessness_debug::protocol::*;
use statelessness_debug::session::{DebugSession, InputPolicy, SessionLimits};
use statelessness_debug::watches::{WatchAuthorization, WatchLimits, WatchRegistry};
use std::io::{Cursor, Read};
fn endpoint(authority: ProtocolAuthority, limits: ProtocolLimits) -> ProtocolSession<RequestModel> {
    ProtocolSession::new(
        DebugSession::new(
            "test",
            RequestModel::fixed(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
        )
        .unwrap(),
        41,
        authority,
        limits,
    )
    .unwrap()
}

fn transact<M: stateless::ModelCodec + stateless::Enumerate>(
    endpoint: &mut ProtocolSession<M>,
    mut request: Request,
) -> Response {
    request.epoch = endpoint.epoch();
    endpoint.handle(request)
}
fn controlled() -> ProtocolSession<RequestModel> {
    endpoint(
        ProtocolAuthority {
            deliver_inputs: true,
            configure_watches: true,
            configure_diagnostics: false,
            read_exact_values: false,
            export_exact_trace: false,
        },
        ProtocolLimits::default(),
    )
}
fn req(id: u64, revision: u64, command: Command) -> Request {
    Request {
        version: 1,
        session: "test".into(),
        epoch: 1,
        request_id: id,
        expected_revision: revision,
        expected_configuration: 0,
        command,
    }
}
fn step(id: u64, revision: u64, input: Input) -> Request {
    req(
        id,
        revision,
        Command::Step {
            encoded_input: RequestModel::fixed().encode_input(&input).unwrap(),
        },
    )
}
fn number(response: &Response, key: &str) -> u64 {
    response
        .fields
        .iter()
        .find(|(k, _)| k == key)
        .unwrap()
        .1
        .strip_prefix("u64:")
        .unwrap()
        .parse()
        .unwrap()
}
#[test]
fn wire_roundtrip_preserves_128_bit_keys_and_terminal_escaping() {
    let limits = ProtocolLimits::default();
    let mut request = req(
        u64::MAX,
        u64::MAX - 1,
        Command::Watch {
            schema: 1,
            baseline_revision: u64::MAX - 1,
            path: vec![
                PathSegment::Field("name\n\x1b[31m<script>東京".into()),
                PathSegment::MapKey(statelessness_debug::inspect::MapKey::Integer {
                    kind: statelessness_debug::inspect::IntegerType::U128,
                    decimal: u128::MAX.to_string(),
                }),
            ],
        },
    );
    request.session = "test\t\x1b[2J<x>".into();
    let bytes = encode_request(&request, &limits).unwrap();
    assert!(bytes.iter().all(|b| b.is_ascii_graphic() || *b == b'\t'));
    assert!(!bytes.contains(&b'<'));
    assert!(!bytes.contains(&27));
    assert_eq!(decode_request(&bytes, &limits).unwrap(), request);
    assert!(
        String::from_utf8(bytes)
            .unwrap()
            .contains("340282366920938463463374607431768211455")
    );
}
#[test]
fn duplicate_request_delivers_once_conflicts_reject_and_retired_ids_do_not_retry() {
    let limits = ProtocolLimits {
        max_cache_entries: 1,
        ..ProtocolLimits::default()
    };
    let mut endpoint = endpoint(
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
        limits,
    );
    let request = step(1, 0, Input::Start);
    let first = transact(&mut endpoint, request.clone());
    assert!(first.result.is_ok());
    assert_eq!(transact(&mut endpoint, request.clone()), first);
    assert_eq!(endpoint.session().sequence(), 1);
    assert_eq!(
        transact(&mut endpoint, req(1, 1, Command::Status)).result,
        Err(ProtocolError::DuplicateConflict)
    );
    transact(&mut endpoint, req(2, 1, Command::Status));
    assert_eq!(
        transact(&mut endpoint, request).result,
        Err(ProtocolError::RetiredRequest)
    );
    assert_eq!(endpoint.session().sequence(), 1);
}
#[test]
fn stale_version_epoch_unauthorized_and_malformed_input_never_deliver() {
    let mut endpoint = controlled();
    let mut request = step(1, 0, Input::Start);
    request.version = 2;
    assert_eq!(
        transact(&mut endpoint, request).result,
        Err(ProtocolError::UnsupportedVersion)
    );
    let mut request = step(1, 0, Input::Start);
    request.epoch = 0;
    assert_eq!(
        endpoint.handle(request).result,
        Err(ProtocolError::WrongEpoch)
    );
    assert_eq!(
        transact(&mut endpoint, step(1, 1, Input::Start)).result,
        Err(ProtocolError::StaleRevision)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                2,
                0,
                Command::Step {
                    encoded_input: vec![255]
                }
            )
        )
        .result,
        Err(ProtocolError::InvalidInput)
    );
    assert_eq!(endpoint.session().sequence(), 0);
    let mut readonly = super_readonly();
    assert_eq!(
        transact(&mut readonly, step(1, 0, Input::Start)).result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(readonly.session().sequence(), 0);
}
fn super_readonly() -> ProtocolSession<RequestModel> {
    endpoint(ProtocolAuthority::default(), ProtocolLimits::default())
}
#[test]
fn snapshot_and_candidate_handles_are_revision_bound() {
    let mut endpoint = controlled();
    endpoint.enable_inspection();
    let snapshot = transact(&mut endpoint, req(1, 0, Command::Snapshot));
    let handle = number(&snapshot, "handle");
    let inputs = transact(
        &mut endpoint,
        req(
            2,
            0,
            Command::Inputs {
                offset: 0,
                limit: 10,
            },
        ),
    );
    assert!(inputs.result.is_ok());
    let token = number(&inputs, "candidates.0.token");
    assert!(
        transact(&mut endpoint, req(3, 0, Command::Select { token }))
            .result
            .is_ok()
    );
    assert_eq!(
        transact(&mut endpoint, req(4, 1, Command::Select { token })).result,
        Err(ProtocolError::StaleHandle)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                5,
                1,
                Command::Inspect {
                    handle,
                    schema: 1,
                    offset: 0,
                    limit: 10,
                    path: vec![]
                }
            )
        )
        .result,
        Err(ProtocolError::StaleHandle)
    );
    assert_eq!(endpoint.session().sequence(), 1);
}
#[test]
fn reconnect_invalidates_epochs_handles_and_never_grants_authority() {
    let mut endpoint = super_readonly();
    let handle = number(
        &transact(&mut endpoint, req(1, 0, Command::Snapshot)),
        "handle",
    );
    endpoint.disconnect();
    assert_eq!(
        transact(&mut endpoint, req(2, 0, Command::Status)).result,
        Err(ProtocolError::Disconnected)
    );
    let old_epoch = endpoint.epoch();
    assert!(endpoint.reconnect().unwrap() > old_epoch);
    assert_eq!(
        endpoint
            .handle(Request {
                epoch: old_epoch,
                ..req(1, 0, Command::Status)
            })
            .result,
        Err(ProtocolError::WrongEpoch)
    );
    let mut current = req(1, 0, Command::Checks { handle });
    current.epoch = 2;
    assert_eq!(
        transact(&mut endpoint, current).result,
        Err(ProtocolError::StaleHandle)
    );
    let mut attempt = step(2, 0, Input::Start);
    attempt.epoch = 2;
    assert_eq!(
        transact(&mut endpoint, attempt).result,
        Err(ProtocolError::Unauthorized)
    );
}
#[test]
fn framed_server_is_linked_to_model_and_exposes_explicit_disconnect() {
    let limits = ProtocolLimits::default();
    let mut endpoint = controlled();
    let mut input = Vec::new();
    for mut request in [
        step(1, 0, Input::Start),
        step(1, 0, Input::Start),
        req(2, 1, Command::Status),
    ] {
        request.epoch = endpoint.epoch();
        write_frame(
            &mut input,
            &encode_request(&request, &limits).unwrap(),
            limits.max_frame_bytes,
        )
        .unwrap();
    }
    let mut output = Vec::new();
    serve(&mut endpoint, &mut Cursor::new(input), &mut output).unwrap();
    assert_eq!(endpoint.session().sequence(), 1);
    let mut output = Cursor::new(output);
    let first = decode_response(
        &read_frame(&mut output, limits.max_frame_bytes)
            .unwrap()
            .unwrap(),
        &limits,
    )
    .unwrap();
    let duplicate = decode_response(
        &read_frame(&mut output, limits.max_frame_bytes)
            .unwrap()
            .unwrap(),
        &limits,
    )
    .unwrap();
    assert_eq!(first, duplicate);
    let status = decode_response(
        &read_frame(&mut output, limits.max_frame_bytes)
            .unwrap()
            .unwrap(),
        &limits,
    )
    .unwrap();
    assert_eq!(status.revision, 1);
    assert!(
        read_frame(&mut output, limits.max_frame_bytes)
            .unwrap()
            .is_none()
    );
    assert_eq!(
        transact(&mut endpoint, req(3, 1, Command::Status)).result,
        Err(ProtocolError::Disconnected)
    );
}
#[test]
fn partial_oversize_invalid_utf8_escape_and_unknown_commands_reject() {
    let limits = ProtocolLimits::default();
    assert!(read_frame(&mut Cursor::new(vec![0, 0]), 1024).is_err());
    assert!(read_frame(&mut Cursor::new(vec![0, 0, 0, 4, 1]), 1024).is_err());
    assert!(read_frame(&mut Cursor::new(u32::MAX.to_be_bytes()), 1024).is_err());
    assert_eq!(
        decode_request(
            b"DDBG\tu32:1\trequest\tstring:test\tu64:1\tu64:1\tu64:0\tu64:0\tstatus\t%GG",
            &limits
        ),
        Err(ProtocolError::Malformed)
    );
    assert_eq!(
        decode_request(
            b"DDBG\tu32:1\trequest\tstring:test\tu64:1\tu64:1\tu64:0\tu64:0\trun_shell",
            &limits
        ),
        Err(ProtocolError::Unsupported)
    );
    assert!(decode_request(&[255], &limits).is_err());
    // Truncating a framed mutator never delivers a partial command.
    let mut endpoint = controlled();
    let encoded = encode_request(&step(1, 0, Input::Start), &limits).unwrap();
    for cut in 0..encoded.len() {
        let mut bytes = (encoded.len() as u32).to_be_bytes().to_vec();
        bytes.extend_from_slice(&encoded[..cut]);
        assert!(serve(&mut endpoint, &mut Cursor::new(bytes), &mut Vec::new()).is_err());
        endpoint.reconnect().unwrap();
        assert_eq!(endpoint.session().sequence(), 0);
    }
}
#[test]
fn runtime_watch_authority_is_separate_and_configuration_is_revisioned() {
    let mut endpoint = endpoint(
        ProtocolAuthority {
            configure_watches: true,
            ..Default::default()
        },
        ProtocolLimits::default(),
    );
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_paths(vec![vec![PathSegment::Field("ready".into())]]),
        ))
        .unwrap();
    let add = transact(
        &mut endpoint,
        req(
            1,
            0,
            Command::Watch {
                schema: 1,
                baseline_revision: 0,
                path: vec![PathSegment::Field("ready".into())],
            },
        ),
    );
    assert!(add.result.is_ok(), "{add:?}");
    assert_eq!(add.configuration, 1);
    assert_eq!(add.revision, 0);
    assert_eq!(number(&add, "watch.effective_sequence"), 1);
    assert_eq!(
        transact(&mut endpoint, step(2, 0, Input::Start)).result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(
        transact(&mut endpoint, req(3, 0, Command::Unwatch { watch_id: 1 })).result,
        Err(ProtocolError::StaleConfiguration)
    );
    let mut disallowed = req(
        4,
        0,
        Command::Watch {
            schema: 1,
            baseline_revision: 0,
            path: vec![PathSegment::Field("pending".into())],
        },
    );
    disallowed.expected_configuration = 1;
    assert_eq!(
        transact(&mut endpoint, disallowed).result,
        Err(ProtocolError::Unauthorized)
    );
    let mut remove = req(5, 0, Command::Unwatch { watch_id: 1 });
    remove.expected_configuration = 1;
    assert!(transact(&mut endpoint, remove).result.is_ok());
    assert_eq!(endpoint.configuration_revision(), 2);
    assert_eq!(endpoint.session().sequence(), 0);
}
#[test]
fn watches_observe_actual_turns_and_do_not_change_exact_model_state() {
    let mut endpoint = controlled();
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all(),
        ))
        .unwrap();
    assert!(
        transact(
            &mut endpoint,
            req(
                1,
                0,
                Command::Watch {
                    schema: 1,
                    baseline_revision: 0,
                    path: vec![PathSegment::Field("active".into())]
                }
            )
        )
        .result
        .is_ok()
    );
    assert!(
        transact(&mut endpoint, step(2, 0, Input::Start))
            .result
            .is_ok()
    );
    let first = transact(
        &mut endpoint,
        req(3, 1, Command::WatchCurrent { watch_id: 1 }),
    );
    assert!(first.result.is_ok());
    assert!(
        first
            .fields
            .iter()
            .any(|(k, v)| k == "watch.kind" && v.contains("Baseline"))
    );
    assert!(
        transact(&mut endpoint, step(4, 1, Input::Cancel))
            .result
            .is_ok()
    );
    let second = transact(
        &mut endpoint,
        req(5, 2, Command::WatchCurrent { watch_id: 1 }),
    );
    assert!(
        second
            .fields
            .iter()
            .any(|(k, v)| k == "watch.kind" && v == "enum:Changed")
    );
    assert_eq!(endpoint.session().sequence(), 2);
    assert!(!endpoint.session().state().active);
}
#[test]
fn events_are_bounded_and_loss_survives_saturated_queue() {
    let limits = ProtocolLimits {
        max_event_entries: 1,
        ..ProtocolLimits::default()
    };
    let mut endpoint = endpoint(
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
        limits,
    );
    transact(&mut endpoint, step(1, 0, Input::Start));
    transact(&mut endpoint, step(2, 1, Input::Cancel));
    let events = transact(&mut endpoint, req(3, 2, Command::Events { limit: 10 }));
    assert_eq!(number(&events, "events.returned"), 1);
    assert_eq!(number(&events, "events.dropped"), 1);
    assert_eq!(
        number(
            &transact(&mut endpoint, req(4, 2, Command::Events { limit: 10 })),
            "events.dropped"
        ),
        1
    );
}
#[test]
fn queued_response_write_failure_does_not_rerun_or_rollback_delivery() {
    struct Broken;
    impl std::io::Write for Broken {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("broken"))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let limits = ProtocolLimits::default();
    let mut endpoint = controlled();
    let mut mutation = step(1, 0, Input::Start);
    mutation.epoch = endpoint.epoch();
    let mut input = Vec::new();
    write_frame(
        &mut input,
        &encode_request(&mutation, &limits).unwrap(),
        limits.max_frame_bytes,
    )
    .unwrap();
    assert!(serve(&mut endpoint, &mut Cursor::new(input), &mut Broken).is_err());
    assert_eq!(endpoint.session().sequence(), 1);
    endpoint.reconnect().unwrap();
    let mut old = step(1, 0, Input::Start);
    old.epoch = 2;
    assert_eq!(
        transact(&mut endpoint, old).result,
        Err(ProtocolError::StaleRevision)
    );
    assert_eq!(endpoint.session().sequence(), 1);
}
#[test]
fn repeated_interrupted_reads_do_not_recurse_and_limits_validate_before_work() {
    struct Interrupts {
        n: usize,
        inner: Cursor<Vec<u8>>,
    }
    impl Read for Interrupts {
        fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
            if self.n > 0 {
                self.n -= 1;
                Err(std::io::Error::from(std::io::ErrorKind::Interrupted))
            } else {
                self.inner.read(b)
            }
        }
    }
    let mut bytes = Vec::new();
    write_frame(&mut bytes, b"ok", 100).unwrap();
    assert_eq!(
        read_frame(
            &mut Interrupts {
                n: 100_000,
                inner: Cursor::new(bytes)
            },
            100
        )
        .unwrap(),
        Some(b"ok".to_vec())
    );
    let mut endpoint = controlled();
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                1,
                0,
                Command::Inputs {
                    offset: 0,
                    limit: usize::MAX
                }
            )
        )
        .result,
        Err(ProtocolError::Limit)
    );
    assert_eq!(endpoint.session().sequence(), 0);
}

#[derive(Clone)]
struct Wide {
    turns: std::rc::Rc<std::cell::Cell<usize>>,
    checks: std::rc::Rc<std::cell::Cell<usize>>,
}
impl stateless::Model for Wide {
    type State = u128;
    type Input = ();
    type Output = u128;
    fn metadata(&self) -> stateless::ModelMetadata {
        stateless::ModelMetadata {
            name: "wide".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<u128, stateless::ModelError> {
        Ok(u128::MAX - 10)
    }
    fn step(
        &self,
        state: &u128,
        _: &(),
    ) -> Result<stateless::Transition<u128, u128>, stateless::ModelError> {
        self.turns.set(self.turns.get() + 1);
        Ok(stateless::Transition::accepted(state + 1, vec![*state + 1]))
    }
    fn check_state(&self, _: &u128) -> Result<Vec<stateless::Check>, stateless::ModelError> {
        self.checks.set(self.checks.get() + 1);
        Ok(vec![stateless::Check::passed("wide")])
    }
}
impl ModelCodec for Wide {
    fn encode_state(&self, s: &u128) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(s.to_le_bytes().to_vec())
    }
    fn decode_state(&self, b: &[u8]) -> Result<u128, stateless::ModelError> {
        Ok(u128::from_le_bytes(
            b.try_into()
                .map_err(|_| stateless::ModelError::new("bad"))?,
        ))
    }
    fn encode_input(&self, _: &()) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![])
    }
    fn decode_input(&self, b: &[u8]) -> Result<(), stateless::ModelError> {
        if b.is_empty() {
            Ok(())
        } else {
            Err(stateless::ModelError::new("bad"))
        }
    }
    fn encode_output(&self, o: &u128) -> Result<Vec<u8>, stateless::ModelError> {
        self.encode_state(o)
    }
}
impl stateless::Enumerate for Wide {
    fn inputs(&self, _: &u128) -> Result<Vec<()>, stateless::ModelError> {
        Ok(vec![()])
    }
}
#[test]
fn structured_inspection_preserves_large_integers_and_only_one_turn_check_runs() {
    let turns = std::rc::Rc::new(std::cell::Cell::new(0));
    let checks = std::rc::Rc::new(std::cell::Cell::new(0));
    let model = Wide {
        turns: turns.clone(),
        checks: checks.clone(),
    };
    let session = DebugSession::new(
        "test",
        model,
        InputPolicy::enumerated(),
        SessionLimits::default(),
    )
    .unwrap();
    let mut endpoint = ProtocolSession::new(
        session,
        1,
        ProtocolAuthority {
            deliver_inputs: true,
            configure_watches: true,
            configure_diagnostics: false,
            read_exact_values: false,
            export_exact_trace: false,
        },
        ProtocolLimits::default(),
    )
    .unwrap();
    endpoint.enable_inspection();
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all(),
        ))
        .unwrap();
    assert!(
        transact(
            &mut endpoint,
            req(
                1,
                0,
                Command::Watch {
                    schema: 1,
                    baseline_revision: 0,
                    path: vec![]
                }
            )
        )
        .result
        .is_ok()
    );
    let snapshot = transact(&mut endpoint, req(2, 0, Command::Snapshot));
    let handle = number(&snapshot, "handle");
    let inspect = transact(
        &mut endpoint,
        req(
            3,
            0,
            Command::Inspect {
                handle,
                schema: 1,
                offset: 0,
                limit: 1,
                path: vec![],
            },
        ),
    );
    assert!(inspect.result.is_ok());
    assert!(
        inspect
            .fields
            .iter()
            .any(|(k, v)| k == "node.value" && v == &format!("u128:{}", u128::MAX - 10))
    );
    let encoded = encode_response(&inspect, endpoint.limits()).unwrap();
    assert_eq!(
        decode_response(&encoded, endpoint.limits()).unwrap(),
        inspect
    );
    let delivery = req(
        4,
        0,
        Command::Step {
            encoded_input: vec![],
        },
    );
    assert!(transact(&mut endpoint, delivery.clone()).result.is_ok());
    transact(&mut endpoint, delivery);
    assert_eq!(turns.get(), 1);
    assert_eq!(checks.get(), 2);
}
#[test]
fn rejected_watch_stale_schema_and_unknown_path_leave_configuration_unchanged() {
    let mut endpoint = controlled();
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all(),
        ))
        .unwrap();
    for (id, schema, path) in [(1, 2, "ready"), (2, 1, "missing")] {
        assert_eq!(
            transact(
                &mut endpoint,
                req(
                    id,
                    0,
                    Command::Watch {
                        schema,
                        baseline_revision: 0,
                        path: vec![PathSegment::Field(path.into())]
                    }
                )
            )
            .result,
            Err(ProtocolError::InvalidInput)
        );
        assert_eq!(endpoint.configuration_revision(), 0);
        assert_eq!(endpoint.session().sequence(), 0);
    }
}
#[test]
fn diagnostics_configuration_does_not_change_exact_trace_bytes() {
    fn recording() -> ProtocolSession<RequestModel> {
        ProtocolSession::new(
            DebugSession::recording(
                "test",
                RequestModel::buggy(),
                InputPolicy::enumerated(),
                SessionLimits::default(),
                stateless::trace::RunConfig::default(),
                stateless::monitor::RecorderOptions::default(),
            )
            .unwrap(),
            1,
            ProtocolAuthority {
                deliver_inputs: true,
                configure_watches: true,
                ..Default::default()
            },
            ProtocolLimits::default(),
        )
        .unwrap()
    }
    let mut ordinary = recording();
    let mut watched = recording();
    watched
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all(),
        ))
        .unwrap();
    transact(
        &mut watched,
        req(
            1,
            0,
            Command::Watch {
                schema: 1,
                baseline_revision: 0,
                path: vec![PathSegment::Field("ready".into())],
            },
        ),
    );
    for (index, input) in [Input::Start, Input::Cancel, Input::Complete(1)]
        .into_iter()
        .enumerate()
    {
        transact(
            &mut ordinary,
            step(index as u64 + 2, index as u64, input.clone()),
        );
        transact(&mut watched, step(index as u64 + 2, index as u64, input));
    }
    assert_eq!(
        ordinary.session().export_trace().unwrap(),
        watched.session().export_trace().unwrap()
    );
}
#[test]
fn response_and_candidate_caps_reject_without_excess_or_hidden_delivery() {
    let limits = ProtocolLimits {
        max_frame_bytes: 2048,
        max_cache_bytes: 4096,
        max_handles: 1,
        max_candidates: 1,
        ..ProtocolLimits::default()
    };
    let mut endpoint = endpoint(
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
        limits,
    );
    let old = number(
        &transact(&mut endpoint, req(1, 0, Command::Snapshot)),
        "handle",
    );
    transact(&mut endpoint, req(2, 0, Command::Snapshot));
    assert_eq!(
        transact(&mut endpoint, req(3, 0, Command::Checks { handle: old })).result,
        Err(ProtocolError::StaleHandle)
    );
    let response = transact(&mut endpoint, req(4, 0, Command::Status));
    assert!(encode_response(&response, endpoint.limits()).unwrap().len() <= 2048);
    // Either a bounded projection or explicit Limit, never a partial state mutation.
    let page = transact(
        &mut endpoint,
        req(
            5,
            0,
            Command::Inputs {
                offset: 0,
                limit: 100,
            },
        ),
    );
    assert!(encode_response(&page, endpoint.limits()).unwrap().len() <= 2048);
    assert_eq!(endpoint.session().sequence(), 0);
}
#[test]
fn bounded_parser_corpus_is_panic_free_and_roundtrips_every_accepted_record() {
    let mut seed = 0x9e37_79b9_u64;
    let limits = ProtocolLimits::default();
    for length in 0..512 {
        let bytes: Vec<u8> = (0..length)
            .map(|_| {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                seed as u8
            })
            .collect();
        if let Ok(request) = decode_request(&bytes, &limits) {
            assert_eq!(
                decode_request(&encode_request(&request, &limits).unwrap(), &limits).unwrap(),
                request
            );
        }
        if let Ok(response) = decode_response(&bytes, &limits) {
            assert_eq!(
                decode_response(&encode_response(&response, &limits).unwrap(), &limits).unwrap(),
                response
            );
        }
    }
    let original = encode_request(&step(1, 0, Input::Start), &limits).unwrap();
    for index in 0..original.len() {
        for replacement in [0, 9, 27, 37, 60, 127, 255] {
            let mut bytes = original.clone();
            bytes[index] = replacement;
            let _ = decode_request(&bytes, &limits);
        }
    }
}

#[derive(Default)]
struct FakeClock(std::sync::atomic::AtomicU64);
impl statelessness_debug::effects::Clock for FakeClock {
    fn now(
        &self,
    ) -> Result<
        statelessness_debug::effects::LocalInstant,
        statelessness_debug::effects::MeasurementError,
    > {
        Ok(statelessness_debug::effects::LocalInstant {
            domain: statelessness_debug::effects::ClockDomain(7),
            nanos: self.0.fetch_add(100, std::sync::atomic::Ordering::Relaxed),
        })
    }
}
fn telemetry() -> statelessness_debug::effects::EffectObserver {
    statelessness_debug::effects::EffectObserver::new(
        statelessness_debug::effects::EffectOptions {
            origin: statelessness_debug::metrics::MeasurementOrigin::TestClock,
            ..Default::default()
        },
        std::sync::Arc::new(FakeClock::default()),
    )
    .unwrap()
}
fn origin(sequence: u64) -> statelessness_debug::effects::RequestOrigin {
    statelessness_debug::effects::RequestOrigin {
        run: 1,
        epoch: 1,
        machine: 1,
        transition_sequence: sequence,
        output_index: 0,
    }
}
#[test]
fn telemetry_queries_need_explicit_adapter_and_never_grant_model_authority() {
    let mut endpoint = super_readonly();
    assert_eq!(
        transact(&mut endpoint, req(1, 0, Command::TelemetryHealth)).result,
        Err(ProtocolError::Unsupported)
    );
    let observer = telemetry();
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let effect = observer.requested(origin(1), Default::default()).unwrap();
    let status = transact(&mut endpoint, req(2, 0, Command::Status));
    assert!(
        status
            .fields
            .iter()
            .any(|(k, v)| k == "capability.effect_telemetry" && v == "bool:true")
    );
    let details = transact(
        &mut endpoint,
        req(3, 0, Command::EffectDetails { id: effect.id() }),
    );
    assert!(details.result.is_ok());
    assert!(
        details
            .fields
            .iter()
            .any(|(k, v)| k == "effect.admission" && v == "unknown")
    );
    assert!(
        details
            .fields
            .iter()
            .any(|(k, v)| k == "effect.running_attempts" && v == "u64:0")
    );
    assert_eq!(
        transact(&mut endpoint, step(4, 0, Input::Start)).result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(endpoint.session().sequence(), 0);
}
#[test]
fn metric_pages_bind_to_immutable_snapshot_and_metric_revision_is_separate() {
    use statelessness_debug::effects::Admission;
    let observer = telemetry();
    let effect = observer.requested(origin(1), Default::default()).unwrap();
    let mut endpoint = super_readonly();
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let first = transact(
        &mut endpoint,
        req(
            1,
            0,
            Command::MetricSnapshot {
                handle: None,
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(first.result.is_ok(), "{first:?}");
    let handle = number(&first, "metrics.handle");
    let metric_revision = number(&first, "metrics.revision");
    assert!(metric_revision > 0);
    assert_eq!(first.revision, 0);
    observer.admission(&effect, Admission::Accepted).unwrap();
    let page = transact(
        &mut endpoint,
        req(
            2,
            0,
            Command::MetricSnapshot {
                handle: Some(handle),
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(page.result.is_ok());
    assert_eq!(number(&page, "metrics.revision"), metric_revision);
    assert_eq!(
        number(&page, "metrics.series.total"),
        number(&first, "metrics.series.total")
    );
    let next = transact(
        &mut endpoint,
        req(
            3,
            0,
            Command::MetricSnapshot {
                handle: None,
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(next.result.is_ok());
    assert!(number(&next, "metrics.revision") > metric_revision);
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                4,
                0,
                Command::MetricSnapshot {
                    handle: Some(handle),
                    offset: 0,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::StaleHandle)
    );
    let window = transact(
        &mut endpoint,
        req(
            5,
            0,
            Command::MetricWindow {
                from: metric_revision,
                to: number(&next, "metrics.revision"),
                limit: 1,
            },
        ),
    );
    assert!(window.result.is_ok(), "{window:?}");
    assert!(
        window
            .fields
            .iter()
            .any(|(k, v)| k == "metrics.temporality" && v == "enum:Delta")
    );
    assert_eq!(endpoint.session().sequence(), 0);
}
#[test]
fn lifecycle_timeline_and_histogram_metadata_remain_host_measured() {
    use statelessness_debug::effects::{Admission, DeliveryDisposition, RuntimeOutcome};
    let observer = telemetry();
    let effect = observer.requested(origin(99), Default::default()).unwrap();
    observer.admission(&effect, Admission::Accepted).unwrap();
    let attempt = observer.attempt_created(&effect).unwrap();
    observer.ready_queued(&attempt).unwrap();
    observer.attempt_started(&attempt).unwrap();
    observer
        .attempt_finished(&attempt, RuntimeOutcome::Success)
        .unwrap();
    observer
        .resolved(&effect, RuntimeOutcome::Success, Some(&attempt))
        .unwrap();
    let delivery = observer.reserve_publication(&effect).unwrap();
    observer.delivery_begun(&delivery).unwrap();
    observer
        .delivery_observed(&delivery, DeliveryDisposition::Ignored)
        .unwrap();
    let mut endpoint = super_readonly();
    endpoint.attach_telemetry(observer.clone()).unwrap();
    let timeline = transact(
        &mut endpoint,
        req(
            1,
            0,
            Command::EffectTimeline {
                id: effect.id(),
                limit: 100,
            },
        ),
    );
    assert!(timeline.result.is_ok(), "{timeline:?}");
    assert!(
        timeline
            .fields
            .iter()
            .any(|(k, v)| k.ends_with(".kind") && v == "enum:attempt_started")
    );
    assert!(
        timeline
            .fields
            .iter()
            .any(|(k, v)| k.ends_with(".disposition") && v == "enum:Ignored")
    );
    assert!(
        timeline
            .fields
            .iter()
            .any(|(k, v)| k == "effect.measurement_origin" && v == "enum:TestClock")
    );
    let first = transact(
        &mut endpoint,
        req(
            2,
            0,
            Command::MetricSnapshot {
                handle: None,
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(first.result.is_ok());
    let handle = number(&first, "metrics.handle");
    let count = number(&first, "metrics.series.total");
    let mut saw_histogram = false;
    for offset in 0..count {
        let page = transact(
            &mut endpoint,
            req(
                offset + 3,
                0,
                Command::MetricSnapshot {
                    handle: Some(handle),
                    offset: offset as usize,
                    limit: 1,
                },
            ),
        );
        assert!(page.result.is_ok(), "{page:?}");
        if page
            .fields
            .iter()
            .any(|(k, v)| k.ends_with(".kind") && v == "enum:Histogram")
        {
            saw_histogram = true;
            assert!(
                page.fields
                    .iter()
                    .any(|(k, v)| k.ends_with(".p99.low_sample_warning") && v == "bool:true")
            );
            assert!(
                page.fields
                    .iter()
                    .any(|(k, v)| k.ends_with(".population") && v.starts_with("string:"))
            );
        }
    }
    assert!(saw_histogram);
    assert_eq!(endpoint.session().sequence(), 0);
}
#[test]
fn telemetry_query_wire_roundtrip_and_missing_history_remain_explicit() {
    let id = statelessness_debug::effects::EffectId {
        run: 1,
        epoch: 1,
        serial: u64::MAX,
    };
    let limits = ProtocolLimits::default();
    for command in [
        Command::MetricCatalog {
            offset: 0,
            limit: 4,
        },
        Command::MetricSnapshot {
            handle: None,
            offset: 0,
            limit: 1,
        },
        Command::MetricSnapshot {
            handle: Some(u64::MAX),
            offset: 1,
            limit: 1,
        },
        Command::MetricWindow {
            from: 3,
            to: 4,
            limit: 2,
        },
        Command::EffectDetails { id },
        Command::EffectTimeline { id, limit: 1 },
        Command::TelemetryHealth,
    ] {
        let request = req(1, 0, command);
        assert_eq!(
            decode_request(&encode_request(&request, &limits).unwrap(), &limits).unwrap(),
            request
        );
    }
    let mut endpoint = super_readonly();
    endpoint.attach_telemetry(telemetry()).unwrap();
    assert_eq!(
        transact(&mut endpoint, req(1, 0, Command::EffectDetails { id })).result,
        Err(ProtocolError::StaleHandle)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                2,
                0,
                Command::MetricWindow {
                    from: 999,
                    to: 1000,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::StaleHandle)
    );
    assert!(
        transact(&mut endpoint, req(3, 0, Command::TelemetryHealth))
            .result
            .is_ok()
    );
}

#[derive(Clone, PartialEq, Eq)]
struct LongSchemaState(u8);
impl statelessness_debug::inspect::Inspect for LongSchemaState {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut statelessness_debug::inspect::InspectContext,
    ) -> Result<statelessness_debug::inspect::InspectNode, statelessness_debug::inspect::InspectError>
    {
        statelessness_debug::inspect::Inspect::inspect(&self.0, path, cx)
    }
    fn schema(&self) -> statelessness_debug::inspect::DisplaySchema {
        statelessness_debug::inspect::DisplaySchema {
            name: "schema_name_longer_than_the_smallest_wire_field_budget_abcdefghijklmnopqrstuvwxyz_abcdefghijklmnopqrstuvwxyz",
            version: 1,
            source: None,
        }
    }
}
struct LongSchemaModel;
impl stateless::Model for LongSchemaModel {
    type State = LongSchemaState;
    type Input = ();
    type Output = ();
    fn metadata(&self) -> stateless::ModelMetadata {
        stateless::ModelMetadata {
            name: "long-schema".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<Self::State, stateless::ModelError> {
        Ok(LongSchemaState(0))
    }
    fn step(
        &self,
        state: &Self::State,
        _: &(),
    ) -> Result<stateless::Transition<Self::State, ()>, stateless::ModelError> {
        Ok(stateless::Transition::accepted(state.clone(), vec![]))
    }
    fn check_state(&self, _: &Self::State) -> Result<Vec<stateless::Check>, stateless::ModelError> {
        Ok(vec![])
    }
}
impl ModelCodec for LongSchemaModel {
    fn encode_state(&self, s: &LongSchemaState) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![s.0])
    }
    fn decode_state(&self, b: &[u8]) -> Result<LongSchemaState, stateless::ModelError> {
        match b {
            [n] => Ok(LongSchemaState(*n)),
            _ => Err(stateless::ModelError::new("bad")),
        }
    }
    fn encode_input(&self, _: &()) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![])
    }
    fn decode_input(&self, b: &[u8]) -> Result<(), stateless::ModelError> {
        if b.is_empty() {
            Ok(())
        } else {
            Err(stateless::ModelError::new("bad"))
        }
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, stateless::ModelError> {
        Ok(vec![])
    }
}
impl stateless::Enumerate for LongSchemaModel {
    fn inputs(&self, _: &LongSchemaState) -> Result<Vec<()>, stateless::ModelError> {
        Ok(vec![()])
    }
}
#[test]
fn watch_admission_ack_cannot_fail_after_mutation_when_schema_metadata_is_large() {
    let limits = ProtocolLimits {
        max_frame_bytes: 2048,
        max_field_bytes: 64,
        max_cache_bytes: 4096,
        ..Default::default()
    };
    let session = DebugSession::new(
        "test",
        LongSchemaModel,
        InputPolicy::enumerated(),
        SessionLimits::default(),
    )
    .unwrap();
    let mut endpoint = ProtocolSession::new(
        session,
        1,
        ProtocolAuthority {
            configure_watches: true,
            ..Default::default()
        },
        limits,
    )
    .unwrap();
    endpoint
        .enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all(),
        ))
        .unwrap();
    let request = req(
        1,
        0,
        Command::Watch {
            schema: 1,
            baseline_revision: 0,
            path: vec![],
        },
    );
    let response = transact(&mut endpoint, request.clone());
    assert!(response.result.is_ok(), "{response:?}");
    assert_eq!(response.configuration, 1);
    assert_eq!(number(&response, "watch.id"), 1);
    assert!(
        response
            .fields
            .iter()
            .any(|(k, v)| k == "watch.metadata_complete" && v == "bool:false")
    );
    assert!(encode_response(&response, endpoint.limits()).unwrap().len() <= 2048);
    assert_eq!(transact(&mut endpoint, request), response);
    assert_eq!(
        endpoint.enable_watches(WatchRegistry::new(
            WatchLimits::default(),
            WatchAuthorization::allow_all()
        )),
        Err(ProtocolError::InvalidInput)
    );
    assert_eq!(endpoint.configuration_revision(), 1);
    assert_eq!(endpoint.session().sequence(), 0);
}

fn recording_endpoint(authority: ProtocolAuthority) -> ProtocolSession<RequestModel> {
    ProtocolSession::new(
        DebugSession::recording(
            "test",
            RequestModel::buggy(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            stateless::trace::RunConfig::default(),
            stateless::monitor::RecorderOptions::default(),
        )
        .unwrap(),
        1,
        authority,
        ProtocolLimits::default(),
    )
    .unwrap()
}
fn bytes_field(response: &Response, key: &str) -> Vec<u8> {
    let value = &response.fields.iter().find(|(k, _)| k == key).unwrap().1;
    let text = value.strip_prefix("bytes:").unwrap();
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
#[test]
fn bounded_raw_export_reconstructs_and_replays_exact_failure_across_chunks() {
    let mut endpoint = recording_endpoint(ProtocolAuthority {
        deliver_inputs: true,
        export_exact_trace: true,
        ..Default::default()
    });
    for (index, input) in [Input::Start, Input::Cancel, Input::Complete(1)]
        .into_iter()
        .enumerate()
    {
        assert!(
            transact(&mut endpoint, step(index as u64 + 1, index as u64, input))
                .result
                .is_ok()
        );
    }
    let handle = number(
        &transact(&mut endpoint, req(4, 3, Command::Snapshot)),
        "handle",
    );
    let mut offset = 0;
    let mut request_id = 5;
    let mut encoded = Vec::new();
    loop {
        let response = transact(
            &mut endpoint,
            req(
                request_id,
                3,
                Command::ExportTrace {
                    handle,
                    offset,
                    limit: 31,
                },
            ),
        );
        request_id += 1;
        assert!(response.result.is_ok(), "{response:?}");
        assert!(
            response
                .fields
                .iter()
                .any(|(k, v)| k == "export.kind" && v == "enum:exact_trace_sensitive")
        );
        assert!(
            response
                .fields
                .iter()
                .any(|(k, v)| k == "export.redacted" && v == "bool:false")
        );
        let chunk = bytes_field(&response, "export.bytes");
        assert!(chunk.len() <= 31);
        assert_eq!(number(&response, "export.offset"), offset);
        encoded.extend_from_slice(&chunk);
        offset += chunk.len() as u64;
        if offset == number(&response, "export.total_bytes") {
            assert!(
                response
                    .fields
                    .iter()
                    .any(|(k, v)| k == "export.complete" && v == "bool:true")
            );
            break;
        }
    }
    let trace = stateless::trace::Trace::read_from(
        Cursor::new(&encoded),
        &stateless::trace::ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(trace, endpoint.session().export_trace().unwrap());
    assert_eq!(
        trace.termination,
        stateless::trace::Termination::PropertyFailed
    );
    let report = stateless::execution::replay(
        endpoint.session().model(),
        &trace,
        stateless::execution::ReplayOptions::default(),
    )
    .unwrap();
    assert!(report.failure_reproduced);
    assert_eq!(report.steps_verified, 3);
    let eof = transact(
        &mut endpoint,
        req(
            request_id,
            3,
            Command::ExportTrace {
                handle,
                offset,
                limit: 1,
            },
        ),
    );
    assert!(eof.result.is_ok());
    assert!(bytes_field(&eof, "export.bytes").is_empty());
}
#[test]
fn raw_export_authority_is_independent_and_stale_handles_offsets_reject() {
    let mut readonly = recording_endpoint(ProtocolAuthority {
        configure_watches: true,
        read_exact_values: true,
        ..Default::default()
    });
    let handle = number(
        &transact(&mut readonly, req(1, 0, Command::Snapshot)),
        "handle",
    );
    assert_eq!(
        transact(
            &mut readonly,
            req(
                2,
                0,
                Command::ExportTrace {
                    handle,
                    offset: 0,
                    limit: 32
                }
            )
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    let mut endpoint = recording_endpoint(ProtocolAuthority {
        deliver_inputs: true,
        export_exact_trace: true,
        ..Default::default()
    });
    let handle = number(
        &transact(&mut endpoint, req(1, 0, Command::Snapshot)),
        "handle",
    );
    let total = endpoint.session().recorder().unwrap().retained_bytes();
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                2,
                0,
                Command::ExportTrace {
                    handle,
                    offset: total + 1,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::InvalidInput)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                3,
                0,
                Command::ExportTrace {
                    handle,
                    offset: u64::MAX,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::Limit)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                4,
                0,
                Command::ExportTrace {
                    handle,
                    offset: 0,
                    limit: usize::MAX
                }
            )
        )
        .result,
        Err(ProtocolError::Limit)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                5,
                0,
                Command::ExportTrace {
                    handle,
                    offset: 0,
                    limit: 0
                }
            )
        )
        .result,
        Err(ProtocolError::Limit)
    );
    transact(&mut endpoint, step(6, 0, Input::Start));
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                7,
                1,
                Command::ExportTrace {
                    handle,
                    offset: 0,
                    limit: 32
                }
            )
        )
        .result,
        Err(ProtocolError::StaleHandle)
    );
    let request = req(
        8,
        1,
        Command::ExportTrace {
            handle: u64::MAX,
            offset: u64::MAX - 1,
            limit: 1,
        },
    );
    assert_eq!(
        decode_request(
            &encode_request(&request, endpoint.limits()).unwrap(),
            endpoint.limits()
        )
        .unwrap(),
        request
    );
}

#[test]
fn recreating_same_display_id_cannot_accept_old_incarnation_commands() {
    let mut first = controlled();
    let mut old = step(1, 0, Input::Start);
    old.epoch = first.epoch();
    assert!(first.handle(old.clone()).result.is_ok());
    first.disconnect();
    let reconnected_epoch = first.reconnect().unwrap();
    let mut old_reconnected = step(1, 1, Input::Cancel);
    old_reconnected.epoch = reconnected_epoch;
    let mut replacement = controlled();
    assert_ne!(replacement.epoch(), first.epoch());
    assert_eq!(
        replacement.handle(old).result,
        Err(ProtocolError::WrongEpoch)
    );
    assert_eq!(
        replacement.handle(old_reconnected).result,
        Err(ProtocolError::WrongEpoch)
    );
    assert_eq!(replacement.session().sequence(), 0);
    let discovery = replacement.handle(Request {
        epoch: 0,
        ..req(1, 0, Command::Status)
    });
    assert!(discovery.result.is_ok());
    assert_eq!(discovery.epoch, replacement.epoch());
    assert_eq!(
        replacement
            .handle(Request {
                epoch: 0,
                ..step(2, 0, Input::Start)
            })
            .result,
        Err(ProtocolError::WrongEpoch)
    );
    let current = Request {
        epoch: discovery.epoch,
        ..step(2, 0, Input::Start)
    };
    assert!(replacement.handle(current).result.is_ok());
    assert_eq!(replacement.session().sequence(), 1);
}

fn probe_profile(id: u64, sink: u64, site: &str) -> ProbeProfile {
    use statelessness_debug::diagnostic::*;
    ProbeProfile {
        id,
        label: format!("profile-{id}"),
        selected: SelectedSubscription {
            subscription: Subscription {
                sink,
                site: site.into(),
                kind: SiteKind::Probe,
                path: vec![],
                trigger: Trigger::Every,
                sample_every: 1,
                minimum_severity: Severity::Debug,
            },
            selector: ScopeSelector::default(),
        },
    }
}
fn probe_hub(
    site: &str,
) -> std::rc::Rc<std::cell::RefCell<statelessness_debug::diagnostic::DiagnosticHub>> {
    use statelessness_debug::diagnostic::*;
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    for sink in [1, 2] {
        hub.add_sink(
            sink,
            SinkPermissions {
                sites: vec![site.into()],
                paths: vec![vec![]],
            },
            SinkLimits::default(),
        )
        .unwrap();
    }
    hub.register_producer(10, 5).unwrap();
    hub.register_producer(20, 6).unwrap();
    std::rc::Rc::new(std::cell::RefCell::new(hub))
}
#[test]
fn runtime_probe_profiles_require_separate_authority_and_real_producer_acknowledgement() {
    use statelessness_debug::diagnostic::*;
    let hub = probe_hub("counter");
    let mut profile = probe_profile(7, 1, "counter");
    profile.selected.selector.machine = Some("worker".into());
    let mut endpoint = endpoint(
        ProtocolAuthority {
            configure_diagnostics: true,
            ..Default::default()
        },
        ProtocolLimits::default(),
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                1,
                0,
                Command::ProbeStatus {
                    offset: 0,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::Unsupported)
    );
    endpoint
        .attach_probes(hub.clone(), vec![profile.clone()], vec![1])
        .unwrap();
    let configure = req(
        2,
        0,
        Command::ProbeConfigure {
            expected_capture_revision: 0,
            profiles: vec![7],
        },
    );
    let ack = transact(&mut endpoint, configure.clone());
    assert!(ack.result.is_ok(), "{ack:?}");
    assert_eq!(number(&ack, "probes.capture_revision"), 1);
    assert_eq!(number(&ack, "probes.pending_producers.total"), 2);
    assert_eq!(transact(&mut endpoint, configure), ack);
    assert_eq!(hub.borrow().revision(), 1);
    assert_eq!(endpoint.configuration_revision(), 0);
    assert_eq!(endpoint.session().sequence(), 0);
    let evaluated = std::cell::Cell::new(0);
    {
        let mut hub = hub.borrow_mut();
        let mut turn = hub.begin_turn(10, 1, 0, DiagnosticOrigin::Live).unwrap();
        turn.probe("counter", || {
            evaluated.set(evaluated.get() + 1);
            42u128
        });
        turn.finish(true);
    }
    assert_eq!(
        evaluated.get(),
        0,
        "configuration acknowledgement cannot be invented by the protocol"
    );
    hub.borrow_mut().acknowledge(10, 1, 1).unwrap();
    let status = transact(
        &mut endpoint,
        req(
            3,
            0,
            Command::ProbeStatus {
                offset: 0,
                limit: 10,
            },
        ),
    );
    assert!(status.result.is_ok());
    assert_eq!(number(&status, "probes.pending_producers.total"), 1);
    {
        let mut hub = hub.borrow_mut();
        let mut turn = hub
            .begin_turn_with_context(
                10,
                1,
                1,
                DiagnosticOrigin::Live,
                CaptureContext {
                    machine: Some("other"),
                    ..Default::default()
                },
            )
            .unwrap();
        turn.probe("counter", || {
            evaluated.set(evaluated.get() + 1);
            42u128
        });
        turn.finish(true);
    }
    assert_eq!(
        evaluated.get(),
        0,
        "static selector must run before payload closure"
    );
    {
        let mut hub = hub.borrow_mut();
        let mut turn = hub
            .begin_turn_with_context(
                10,
                1,
                2,
                DiagnosticOrigin::Live,
                CaptureContext {
                    machine: Some("worker"),
                    ..Default::default()
                },
            )
            .unwrap();
        turn.probe("counter", || {
            evaluated.set(evaluated.get() + 1);
            42u128
        });
        turn.finish(true);
    }
    assert_eq!(evaluated.get(), 1);
    let poll = req(
        4,
        0,
        Command::ProbeEvents {
            sink: 1,
            limit: 10,
            include_payload: true,
        },
    );
    let events = transact(&mut endpoint, poll.clone());
    assert!(events.result.is_ok(), "{events:?}");
    assert_eq!(number(&events, "probes.events.returned"), 1);
    assert!(
        events
            .fields
            .iter()
            .any(|(k, v)| k.ends_with(".payload.value") && v == "u128:42")
    );
    assert_eq!(hub.borrow().queued(1).unwrap().0, 0);
    assert_eq!(transact(&mut endpoint, poll), events);
    assert_eq!(
        transact(&mut endpoint, step(5, 0, Input::Start)).result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                6,
                0,
                Command::ProbeEvents {
                    sink: 2,
                    limit: 1,
                    include_payload: true
                }
            )
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    let mut reader = super_readonly();
    reader
        .attach_probes(hub.clone(), vec![profile], vec![1])
        .unwrap();
    assert_eq!(
        transact(
            &mut reader,
            req(
                1,
                0,
                Command::ProbeConfigure {
                    expected_capture_revision: 1,
                    profiles: vec![]
                }
            )
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(hub.borrow().revision(), 1);
}
#[test]
fn probe_profile_stale_unknown_denied_and_busy_paths_do_not_mutate_capture() {
    let hub = probe_hub("counter");
    let mut endpoint = endpoint(
        ProtocolAuthority {
            configure_diagnostics: true,
            ..Default::default()
        },
        ProtocolLimits::default(),
    );
    endpoint
        .attach_probes(
            hub.clone(),
            vec![
                probe_profile(7, 1, "counter"),
                probe_profile(9, 1, "forbidden"),
            ],
            vec![1],
        )
        .unwrap();
    for (id, command, error) in [
        (
            1,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![999],
            },
            ProtocolError::Unauthorized,
        ),
        (
            2,
            Command::ProbeConfigure {
                expected_capture_revision: 1,
                profiles: vec![7],
            },
            ProtocolError::StaleConfiguration,
        ),
        (
            3,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![7, 7],
            },
            ProtocolError::InvalidInput,
        ),
        (
            4,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![9],
            },
            ProtocolError::Unauthorized,
        ),
    ] {
        assert_eq!(
            transact(&mut endpoint, req(id, 0, command)).result,
            Err(error)
        );
        assert_eq!(hub.borrow().revision(), 0);
    }
    let active = hub.borrow_mut();
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                5,
                0,
                Command::ProbeStatus {
                    offset: 0,
                    limit: 1
                }
            )
        )
        .result,
        Err(ProtocolError::Busy)
    );
    drop(active);
    let status = transact(
        &mut endpoint,
        req(
            6,
            0,
            Command::ProbeStatus {
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(status.result.is_ok());
    endpoint.disconnect();
    endpoint.reconnect().unwrap();
    assert_eq!(
        transact(
            &mut endpoint,
            req(
                1,
                0,
                Command::ProbeConfigure {
                    expected_capture_revision: 0,
                    profiles: vec![999]
                }
            )
        )
        .result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(hub.borrow().revision(), 0);
}
#[test]
fn oversized_probe_payload_is_not_consumed_and_summary_fits_smallest_frame() {
    use statelessness_debug::diagnostic::*;
    let site = "x".repeat(256);
    let hub = probe_hub(&site);
    let limits = ProtocolLimits {
        max_frame_bytes: 2048,
        max_field_bytes: 64,
        max_cache_bytes: 4096,
        ..Default::default()
    };
    let mut endpoint = endpoint(
        ProtocolAuthority {
            configure_diagnostics: true,
            ..Default::default()
        },
        limits,
    );
    endpoint
        .attach_probes(hub.clone(), vec![probe_profile(7, 1, &site)], vec![1])
        .unwrap();
    let configured = transact(
        &mut endpoint,
        req(
            1,
            0,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![7],
            },
        ),
    );
    assert!(configured.result.is_ok(), "{configured:?}");
    hub.borrow_mut().acknowledge(10, 1, 0).unwrap();
    {
        let mut hub = hub.borrow_mut();
        let mut turn = hub.begin_turn(10, 1, 0, DiagnosticOrigin::Live).unwrap();
        turn.probe(&site, || "secret-looking-long-value".repeat(100));
        turn.finish(true);
    }
    assert_eq!(hub.borrow().queued(1).unwrap().0, 1);
    let full = transact(
        &mut endpoint,
        req(
            2,
            0,
            Command::ProbeEvents {
                sink: 1,
                limit: 1,
                include_payload: true,
            },
        ),
    );
    assert_eq!(full.result, Err(ProtocolError::Limit));
    assert_eq!(hub.borrow().queued(1).unwrap().0, 1);
    let summary = transact(
        &mut endpoint,
        req(
            3,
            0,
            Command::ProbeEvents {
                sink: 1,
                limit: 1,
                include_payload: false,
            },
        ),
    );
    assert!(summary.result.is_ok(), "{summary:?}");
    assert_eq!(number(&summary, "probes.events.returned"), 1);
    assert!(
        summary
            .fields
            .iter()
            .any(|(k, v)| k.ends_with("metadata_omitted") && v == "bool:true")
    );
    assert!(encode_response(&summary, endpoint.limits()).unwrap().len() <= 2048);
    assert_eq!(hub.borrow().queued(1).unwrap().0, 0);
    let status = transact(
        &mut endpoint,
        req(
            4,
            0,
            Command::ProbeStatus {
                offset: 0,
                limit: 1,
            },
        ),
    );
    assert!(status.result.is_ok(), "{status:?}");
    assert!(encode_response(&status, endpoint.limits()).unwrap().len() <= 2048);
}
#[test]
fn maximum_probe_catalog_and_producer_status_can_be_paged_under_small_frame() {
    use statelessness_debug::diagnostic::*;
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    for sink in 1..=16 {
        hub.add_sink(sink, SinkPermissions::local_all(), SinkLimits::default())
            .unwrap();
    }
    for producer in 1..=64 {
        hub.register_producer(producer, producer).unwrap();
    }
    let hub = std::rc::Rc::new(std::cell::RefCell::new(hub));
    let profiles = (1..=64).map(|id| probe_profile(id, 1, "counter")).collect();
    let limits = ProtocolLimits {
        max_frame_bytes: 2048,
        max_field_bytes: 64,
        max_cache_bytes: 4096,
        ..Default::default()
    };
    let mut endpoint = endpoint(ProtocolAuthority::default(), limits);
    endpoint
        .attach_probes(hub, profiles, (1..=16).collect())
        .unwrap();
    for offset in 0..64 {
        let response = transact(
            &mut endpoint,
            req(
                offset as u64 + 1,
                0,
                Command::ProbeStatus { offset, limit: 1 },
            ),
        );
        assert!(response.result.is_ok(), "offset {offset}: {response:?}");
        assert_eq!(number(&response, "probes.profiles.total"), 64);
        assert_eq!(number(&response, "probes.producers.total"), 64);
        assert!(encode_response(&response, endpoint.limits()).unwrap().len() <= 2048);
    }
}
#[test]
fn probe_wire_roundtrip_and_framed_configuration_leave_workers_pending() {
    let hub = probe_hub("counter");
    let mut endpoint = endpoint(
        ProtocolAuthority {
            configure_diagnostics: true,
            ..Default::default()
        },
        ProtocolLimits::default(),
    );
    endpoint
        .attach_probes(hub.clone(), vec![probe_profile(7, 1, "counter")], vec![1])
        .unwrap();
    let mut wire = Vec::new();
    let limits = ProtocolLimits::default();
    for (id, command) in [
        (
            1,
            Command::ProbeStatus {
                offset: 0,
                limit: 1,
            },
        ),
        (
            2,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![7],
            },
        ),
        (
            3,
            Command::ProbeEvents {
                sink: 1,
                limit: 1,
                include_payload: false,
            },
        ),
    ] {
        let request = Request {
            epoch: endpoint.epoch(),
            ..req(id, 0, command)
        };
        let bytes = encode_request(&request, &limits).unwrap();
        assert_eq!(decode_request(&bytes, &limits).unwrap(), request);
        write_frame(&mut wire, &bytes, limits.max_frame_bytes).unwrap();
    }
    let mut output = Vec::new();
    serve(&mut endpoint, &mut Cursor::new(wire), &mut output).unwrap();
    assert_eq!(hub.borrow().revision(), 1);
    assert_eq!(hub.borrow().pending_producers(), vec![10, 20]);
    assert_eq!(endpoint.session().sequence(), 0);
    let mut output = Cursor::new(output);
    while let Some(frame) = read_frame(&mut output, limits.max_frame_bytes).unwrap() {
        assert!(decode_response(&frame, &limits).unwrap().result.is_ok());
    }
}
