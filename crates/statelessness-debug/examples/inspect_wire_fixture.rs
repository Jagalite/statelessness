//! Small deterministic host used by the native inspection wire differential.
use stateless::{Check, Enumerate, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use statelessness_debug::diagnostic::{
    DiagnosticHub, DiagnosticLimits, ScopeSelector, SelectedSubscription, Severity, SinkLimits,
    SinkPermissions, SiteKind, Subscription, Trigger,
};
use statelessness_debug::effects::{
    Admission, Clock, ClockDomain, EffectObserver, EffectOptions, LocalInstant, MeasurementError,
    RequestOrigin,
};
use statelessness_debug::inspect::{
    DisplaySchema, FieldView, Inspect, InspectContext, InspectError, InspectNode, PathSegment,
};
use statelessness_debug::metrics::{Labels, MeasurementOrigin};
use statelessness_debug::protocol::ProbeProfile;
use statelessness_debug::protocol::{
    Command, ProtocolAuthority, ProtocolLimits, ProtocolSession, Request, encode_response,
};
use statelessness_debug::session::{DebugSession, InputPolicy, SessionLimits};
use statelessness_debug::watches::{WatchAuthorization, WatchLimits, WatchRegistry};
use std::sync::Arc;
use std::{cell::RefCell, rc::Rc};

struct FixedClock;
impl Clock for FixedClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        Ok(LocalInstant {
            domain: ClockDomain(7),
            nanos: 0,
        })
    }
}

struct Wide;
impl Model for Wide {
    type State = u128;
    type Input = ();
    type Output = u128;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "wire-wide".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture".into(),
        }
    }
    fn initial_state(&self) -> Result<u128, ModelError> {
        Ok(u128::MAX - 10)
    }
    fn step(&self, s: &u128, _: &()) -> Result<Transition<u128, u128>, ModelError> {
        Ok(Transition::accepted(*s, vec![]))
    }
    fn check_state(&self, _: &u128) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl ModelCodec for Wide {
    fn encode_state(&self, s: &u128) -> Result<Vec<u8>, ModelError> {
        Ok(s.to_le_bytes().to_vec())
    }
    fn decode_state(&self, b: &[u8]) -> Result<u128, ModelError> {
        Ok(u128::from_le_bytes(
            b.try_into().map_err(|_| ModelError::new("state"))?,
        ))
    }
    fn encode_input(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
    fn decode_input(&self, b: &[u8]) -> Result<(), ModelError> {
        if b.is_empty() {
            Ok(())
        } else {
            Err(ModelError::new("input"))
        }
    }
    fn encode_output(&self, o: &u128) -> Result<Vec<u8>, ModelError> {
        self.encode_state(o)
    }
}
impl Enumerate for Wide {
    fn inputs(&self, _: &u128) -> Result<Vec<()>, ModelError> {
        Ok(vec![()])
    }
}

#[derive(Clone, PartialEq, Eq)]
struct Child {
    x: u64,
    y: u64,
}
impl Inspect for Child {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            path,
            "Child",
            &[
                FieldView::new("x", "x", &self.x),
                FieldView::new("y", "y", &self.y),
            ],
        )
    }
}
#[derive(Clone, PartialEq, Eq)]
struct TreeState {
    first: u64,
    child: Child,
}
impl Inspect for TreeState {
    fn schema(&self) -> DisplaySchema {
        DisplaySchema {
            name: "fixture.tree",
            version: 1,
            source: None,
        }
    }
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            path,
            "Tree",
            &[
                FieldView::new("first", "first", &self.first),
                FieldView::new("child", "child", &self.child),
            ],
        )
    }
}
struct Tree;
impl Model for Tree {
    type State = TreeState;
    type Input = ();
    type Output = u64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "wire-tree".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture".into(),
        }
    }
    fn initial_state(&self) -> Result<TreeState, ModelError> {
        Ok(TreeState {
            first: 7,
            child: Child { x: 11, y: 12 },
        })
    }
    fn step(&self, s: &TreeState, _: &()) -> Result<Transition<TreeState, u64>, ModelError> {
        Ok(Transition::accepted(s.clone(), vec![]))
    }
    fn check_state(&self, _: &TreeState) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
impl ModelCodec for Tree {
    fn encode_state(&self, s: &TreeState) -> Result<Vec<u8>, ModelError> {
        Ok([
            s.first.to_le_bytes(),
            s.child.x.to_le_bytes(),
            s.child.y.to_le_bytes(),
        ]
        .concat())
    }
    fn decode_state(&self, b: &[u8]) -> Result<TreeState, ModelError> {
        if b.len() != 24 {
            return Err(ModelError::new("state"));
        }
        let n = |i| u64::from_le_bytes(b[i..i + 8].try_into().unwrap());
        Ok(TreeState {
            first: n(0),
            child: Child { x: n(8), y: n(16) },
        })
    }
    fn encode_input(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
    fn decode_input(&self, b: &[u8]) -> Result<(), ModelError> {
        if b.is_empty() {
            Ok(())
        } else {
            Err(ModelError::new("input"))
        }
    }
    fn encode_output(&self, o: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(o.to_le_bytes().to_vec())
    }
}
impl Enumerate for Tree {
    fn inputs(&self, _: &TreeState) -> Result<Vec<()>, ModelError> {
        Ok(vec![()])
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let session = DebugSession::new(
        "wire",
        Wide,
        InputPolicy::enumerated(),
        SessionLimits::default(),
    )?;
    let mut p = ProtocolSession::new(
        session,
        1,
        ProtocolAuthority::default(),
        ProtocolLimits::default(),
    )
    .map_err(|e| format!("{e:?}"))?;
    p.enable_inspection();
    let request = |id, command| Request {
        version: 1,
        session: "wire".into(),
        epoch: 1,
        request_id: id,
        expected_revision: 0,
        expected_configuration: 0,
        command,
    };
    let snapshot = p.handle(request(1, Command::Snapshot));
    let handle = snapshot
        .fields
        .iter()
        .find(|(k, _)| k == "handle")
        .ok_or("missing handle")?
        .1
        .strip_prefix("u64:")
        .ok_or("bad handle")?
        .parse()?;
    for (id, schema) in [(2, 1), (3, 2)] {
        let response = p.handle(request(
            id,
            Command::Inspect {
                handle,
                schema,
                offset: 0,
                limit: 1,
                path: vec![],
            },
        ));
        println!(
            "{}",
            String::from_utf8(
                encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?
            )?
        );
    }
    let session = DebugSession::new(
        "tree",
        Tree,
        InputPolicy::enumerated(),
        SessionLimits::default(),
    )?;
    let mut p = ProtocolSession::new(
        session,
        2,
        ProtocolAuthority::default(),
        ProtocolLimits::default(),
    )
    .map_err(|e| format!("{e:?}"))?;
    p.enable_inspection();
    let request = |id, command| Request {
        version: 1,
        session: "tree".into(),
        epoch: 2,
        request_id: id,
        expected_revision: 0,
        expected_configuration: 0,
        command,
    };
    let snapshot = p.handle(request(1, Command::Snapshot));
    let handle = snapshot
        .fields
        .iter()
        .find(|(k, _)| k == "handle")
        .ok_or("missing handle")?
        .1
        .strip_prefix("u64:")
        .ok_or("bad handle")?
        .parse()?;
    for (id, offset, limit, path) in [
        (2, 1, 1, vec![]),
        (3, 0, 1, vec![PathSegment::Field("child".into())]),
    ] {
        let response = p.handle(request(
            id,
            Command::Inspect {
                handle,
                schema: 1,
                offset,
                limit,
                path,
            },
        ));
        println!(
            "{}",
            String::from_utf8(
                encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?
            )?
        );
    }
    let observer = EffectObserver::new(
        EffectOptions {
            origin: MeasurementOrigin::TestClock,
            ..Default::default()
        },
        Arc::new(FixedClock),
    )
    .map_err(|e| format!("{e:?}"))?;
    let effect = observer.requested(
        RequestOrigin {
            run: 1,
            epoch: 1,
            machine: 1,
            transition_sequence: 1,
            output_index: 0,
        },
        Labels::default(),
    )?;
    observer.debugger_pause(true);
    observer.admission(&effect, Admission::Accepted)?;
    let attempt = observer.attempt_created(&effect)?;
    observer.attempt_started(&attempt)?;
    let session = DebugSession::new(
        "effect",
        Wide,
        InputPolicy::enumerated(),
        SessionLimits::default(),
    )?;
    let mut p = ProtocolSession::new(
        session,
        3,
        ProtocolAuthority::default(),
        ProtocolLimits::default(),
    )
    .map_err(|e| format!("{e:?}"))?;
    p.attach_telemetry(observer).map_err(|e| format!("{e:?}"))?;
    let request = |id, command| Request {
        version: 1,
        session: "effect".into(),
        epoch: 3,
        request_id: id,
        expected_revision: 0,
        expected_configuration: 0,
        command,
    };
    for (id, command) in [
        (1, Command::EffectDetails { id: effect.id() }),
        (
            2,
            Command::EffectTimeline {
                id: effect.id(),
                limit: 2,
            },
        ),
    ] {
        let response = p.handle(request(id, command));
        println!(
            "{}",
            String::from_utf8(
                encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?
            )?
        );
    }
    let session = DebugSession::new(
        "watches",
        Wide,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
    )?;
    let limits = ProtocolLimits {
        max_response_fields: 32,
        ..Default::default()
    };
    let authority = ProtocolAuthority {
        configure_watches: true,
        deliver_inputs: true,
        ..Default::default()
    };
    let mut p =
        ProtocolSession::new(session, 4, authority, limits).map_err(|e| format!("{e:?}"))?;
    p.enable_watches(WatchRegistry::new(
        WatchLimits::default(),
        WatchAuthorization::allow_all(),
    ))
    .map_err(|e| format!("{e:?}"))?;
    for n in 0..7 {
        let response = p.handle(Request {
            version: 1,
            session: "watches".into(),
            epoch: 4,
            request_id: n + 1,
            expected_revision: 0,
            expected_configuration: n,
            command: Command::Watch {
                schema: 1,
                baseline_revision: 0,
                path: vec![],
            },
        });
        if response.result.is_err() {
            return Err(format!("watch install: {:?}", response.result).into());
        }
    }
    let response = p.handle(Request {
        version: 1,
        session: "watches".into(),
        epoch: 4,
        request_id: 8,
        expected_revision: 0,
        expected_configuration: 7,
        command: Command::WatchStatus,
    });
    println!(
        "{}",
        String::from_utf8(encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?)?
    );
    let stepped = p.handle(Request {
        version: 1,
        session: "watches".into(),
        epoch: 4,
        request_id: 9,
        expected_revision: 0,
        expected_configuration: 7,
        command: Command::Step {
            encoded_input: vec![],
        },
    });
    if stepped.result.is_err() {
        return Err(format!("watch step: {:?}", stepped.result).into());
    }
    let response = p.handle(Request {
        version: 1,
        session: "watches".into(),
        epoch: 4,
        request_id: 10,
        expected_revision: 1,
        expected_configuration: 7,
        command: Command::WatchCurrent { watch_id: 1 },
    });
    println!(
        "{}",
        String::from_utf8(encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?)?
    );
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(
        1,
        SinkPermissions {
            sites: vec!["site".into()],
            paths: vec![vec![]],
        },
        SinkLimits::default(),
    )?;
    for id in 0..40 {
        hub.register_producer(id, 1)?;
    }
    let hub = Rc::new(RefCell::new(hub));
    let profile = ProbeProfile {
        id: 1,
        label: "marker".into(),
        selected: SelectedSubscription {
            subscription: Subscription {
                sink: 1,
                site: "site".into(),
                kind: SiteKind::Marker,
                path: vec![],
                trigger: Trigger::Every,
                sample_every: 1,
                minimum_severity: Severity::Info,
            },
            selector: ScopeSelector::default(),
        },
    };
    let session = DebugSession::new(
        "probes",
        Wide,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
    )?;
    let mut p = ProtocolSession::new(
        session,
        5,
        ProtocolAuthority {
            configure_diagnostics: true,
            ..Default::default()
        },
        ProtocolLimits {
            max_response_fields: 32,
            ..Default::default()
        },
    )
    .map_err(|e| format!("{e:?}"))?;
    p.attach_probes(hub, vec![profile], vec![1])
        .map_err(|e| format!("{e:?}"))?;
    let request = |id, command| Request {
        version: 1,
        session: "probes".into(),
        epoch: 5,
        request_id: id,
        expected_revision: 0,
        expected_configuration: 0,
        command,
    };
    for (id, command) in [
        (
            1,
            Command::ProbeConfigure {
                expected_capture_revision: 0,
                profiles: vec![1],
            },
        ),
        (
            2,
            Command::ProbeStatus {
                offset: 0,
                limit: 1,
            },
        ),
    ] {
        let response = p.handle(request(id, command));
        println!(
            "{}",
            String::from_utf8(
                encode_response(&response, p.limits()).map_err(|e| format!("{e:?}"))?
            )?
        );
    }
    Ok(())
}
