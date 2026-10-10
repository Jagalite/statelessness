//! Reproducible diagnostic-only microbenchmark. No real effects are measured.
//! Run: cargo run -p statelessness-debug --release --example measure_watches
use statelessness_debug::diagnostic::{
    DiagnosticHub, DiagnosticLimits, DiagnosticOrigin, Severity, SinkLimits, SinkPermissions,
    SiteKind, Subscription, Trigger,
};
use statelessness_debug::inspect::{Inspect, SnapshotId};
use statelessness_debug::watches::*;
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Instant;
struct Allocator;
static MEASURE: AtomicBool = AtomicBool::new(false);
static ALLOCS: AtomicU64 = AtomicU64::new(0);
// SAFETY: All allocations and deallocations are forwarded unchanged to System.
unsafe impl GlobalAlloc for Allocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if MEASURE.load(Ordering::Relaxed) {
            ALLOCS.fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: Forwarded allocator contract and unchanged layout.
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: The caller supplies System's live pointer and original layout.
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if MEASURE.load(Ordering::Relaxed) {
            ALLOCS.fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: Forwarded allocator contract and unchanged pointer/layout/new size.
        unsafe { System.realloc(ptr, layout, new_size) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        if MEASURE.load(Ordering::Relaxed) {
            ALLOCS.fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: Forwarded allocator contract and unchanged layout.
        unsafe { System.alloc_zeroed(layout) }
    }
}
#[global_allocator]
static ALLOCATOR: Allocator = Allocator;
fn measure(name: &str, turns: u64, mut run: impl FnMut(u64)) {
    ALLOCS.store(0, Ordering::Relaxed);
    let start = Instant::now();
    MEASURE.store(true, Ordering::Relaxed);
    for sequence in 0..turns {
        run(sequence);
    }
    MEASURE.store(false, Ordering::Relaxed);
    let elapsed = start.elapsed();
    let allocations = ALLOCS.load(Ordering::Relaxed);
    println!(
        "{name}: turns={turns} ns_per_observation={} allocations={} allocations_per_observation={:.3}",
        elapsed.as_nanos() / u128::from(turns),
        allocations,
        allocations as f64 / turns as f64
    );
}
fn context(sequence: u64) -> ObservationContext<'static> {
    ObservationContext::new(
        SnapshotId {
            session: 1,
            revision: sequence,
            sequence,
        },
        OriginKind::Simulation,
    )
}
fn registry(watches: usize, enabled: bool) -> WatchRegistry {
    let mut r = WatchRegistry::new(WatchLimits::default(), WatchAuthorization::allow_all());
    for _ in 0..watches {
        let mut config = WatchConfig::changed(vec![], 1);
        config.enabled = enabled;
        r.add(config).unwrap();
    }
    r
}
fn diagnostic(subscribers: usize, records: usize) -> DiagnosticHub {
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(
        1,
        SinkPermissions::local_all(),
        SinkLimits {
            records,
            ..SinkLimits::default()
        },
    )
    .unwrap();
    hub.register_producer(1, 1).unwrap();
    let subscriptions = (0..subscribers)
        .map(|_| Subscription {
            sink: 1,
            site: "counter".into(),
            kind: SiteKind::Probe,
            path: vec![],
            trigger: Trigger::Every,
            sample_every: 1,
            minimum_severity: Severity::Debug,
        })
        .collect();
    let ack = hub.configure(0, subscriptions).unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    hub
}
fn main() {
    println!(
        "watch benchmark; profile=release; one producer; no exporter I/O; no model execution; {:?}-{}",
        std::env::consts::ARCH,
        std::env::consts::OS
    );
    let mut off = registry(64, false);
    measure("64_runtime_disabled", 100_000, |seq| {
        std::hint::black_box(off.observe(&seq, context(seq)));
    });
    let mut one = registry(1, true);
    measure("one_changed_scalar", 10_000, |seq| {
        std::hint::black_box(one.observe(&seq, context(seq)));
    });
    let mut many = registry(64, true);
    measure("64_shared_scalar", 2_000, |seq| {
        std::hint::black_box(many.observe(&seq, context(seq)));
    });
    let large: Vec<u128> = (0..100_000).collect();
    let mut subtree = registry(1, true);
    measure("large_subtree_page100", 2_000, |seq| {
        std::hint::black_box(subtree.observe(&large as &dyn Inspect, context(seq)));
    });
    let mut failing = registry(1, true);
    measure("failure_ring_snapshot", 2_000, |seq| {
        let mut c = context(seq);
        c.property_failure = seq.is_multiple_of(100);
        std::hint::black_box(failing.observe(&seq, c));
    });
    println!(
        "retained ring bytes: disabled={} scalar={} many={} subtree={} failure={}",
        off.snapshot().retained_bytes,
        one.snapshot().retained_bytes,
        many.snapshot().retained_bytes,
        subtree.snapshot().retained_bytes,
        failing.snapshot().retained_bytes
    );
    let mut disabled_probe = diagnostic(0, 1024);
    measure("runtime_disabled_scoped_probe", 100_000, |seq| {
        let mut scope = disabled_probe
            .begin_turn(1, 1, seq, DiagnosticOrigin::Simulation)
            .unwrap();
        scope.probe("counter", || seq);
        scope.finish(true);
    });
    let mut scalar_probe = diagnostic(1, 1024);
    measure("one_scalar_probe_and_drain", 10_000, |seq| {
        let mut scope = scalar_probe
            .begin_turn(1, 1, seq, DiagnosticOrigin::Simulation)
            .unwrap();
        scope.probe("counter", || seq);
        scope.finish(true);
        std::hint::black_box(scalar_probe.pop(1));
    });
    let mut many_probe = diagnostic(64, 1024);
    measure("64_probe_subscribers_and_drain", 2_000, |seq| {
        let mut scope = many_probe
            .begin_turn(1, 1, seq, DiagnosticOrigin::Simulation)
            .unwrap();
        scope.probe("counter", || seq);
        scope.finish(true);
        while let Some(event) = many_probe.pop(1) {
            std::hint::black_box(event);
        }
    });
    let mut saturated_probe = diagnostic(1, 1);
    measure("saturated_probe_drop_newest", 10_000, |seq| {
        let mut scope = saturated_probe
            .begin_turn(1, 1, seq, DiagnosticOrigin::Simulation)
            .unwrap();
        scope.probe("counter", || seq);
        scope.finish(true);
    });
    println!(
        "saturated sink health: {:?}",
        saturated_probe.health(1).unwrap()
    );
}
