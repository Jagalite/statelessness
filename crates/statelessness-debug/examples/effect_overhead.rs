//! Reproducible local host-hook microbenchmark, not a production SLA benchmark.
//! Run: cargo run -p statelessness-debug --example effect_overhead --release --offline
use statelessness_debug::{effects::*, metrics::*};
use std::{
    alloc::{GlobalAlloc, Layout, System},
    hint::black_box,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Instant,
};
struct CountingAllocator;
static ALLOCS: AtomicU64 = AtomicU64::new(0);
static REALLOCS: AtomicU64 = AtomicU64::new(0);
static ALLOCATED: AtomicU64 = AtomicU64::new(0);
// SAFETY: Every allocation operation delegates unchanged to the system
// allocator. The extra atomics neither dereference nor retain any pointer.
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        ALLOCATED.fetch_add(layout.size() as u64, Ordering::Relaxed);
        // SAFETY: Forwarding the caller's valid layout to System.
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: Forwarding the original pointer and layout unchanged.
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        REALLOCS.fetch_add(1, Ordering::Relaxed);
        ALLOCATED.fetch_add(size as u64, Ordering::Relaxed);
        // SAFETY: Forwarding the valid allocation and requested size unchanged.
        unsafe { System.realloc(ptr, layout, size) }
    }
}
#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;
struct CountingClock {
    inner: MonotonicClock,
    reads: AtomicU64,
}
impl Clock for CountingClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        self.reads.fetch_add(1, Ordering::Relaxed);
        self.inner.now()
    }
}
fn work(n: u64) -> u64 {
    black_box(n.wrapping_mul(31).wrapping_add(7))
}
fn turn(o: &EffectObserver, n: u64) {
    let e = o
        .requested(
            RequestOrigin {
                run: 1,
                epoch: 1,
                machine: 1,
                transition_sequence: n,
                output_index: 0,
            },
            Labels::default(),
        )
        .unwrap();
    o.admission(&e, Admission::Accepted).unwrap();
    let a = o.attempt_created(&e).unwrap();
    o.ready_queued(&a).unwrap();
    o.attempt_started(&a).unwrap();
    black_box(work(n));
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    let d = o.reserve_publication(&e).unwrap();
    o.publication_committed(&d).unwrap();
    o.delivery_begun(&d).unwrap();
    o.delivery_observed(&d, DeliveryDisposition::Accepted)
        .unwrap();
}
fn main() {
    let n = std::env::args()
        .nth(1)
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(20_000);
    println!(
        "scenario,iterations,ns_per_effect,effects_per_second,allocations,reallocations,allocated_bytes,clock_reads,concurrency,unfinished,aggregate_bytes,detail_bytes,detail_losses"
    );
    for name in [
        "compiled_out",
        "runtime_disabled",
        "counters_only",
        "timings_only",
        "full_detail",
        "saturated_detail",
    ] {
        let clock = Arc::new(CountingClock {
            inner: MonotonicClock::new(ClockDomain(1)),
            reads: AtomicU64::new(0),
        });
        let options = EffectOptions {
            counters: name != "runtime_disabled",
            timings: !matches!(name, "runtime_disabled" | "counters_only"),
            details: matches!(name, "full_detail" | "saturated_detail"),
            max_details: if name == "saturated_detail" { 8 } else { 4096 },
            max_detail_bytes: if name == "saturated_detail" {
                4096
            } else {
                8 * 1024 * 1024
            },
            ..EffectOptions::default()
        };
        let observer = EffectObserver::new(options, clock.clone()).unwrap();
        if name != "compiled_out" {
            turn(&observer, 0);
        }
        ALLOCS.store(0, Ordering::Relaxed);
        REALLOCS.store(0, Ordering::Relaxed);
        ALLOCATED.store(0, Ordering::Relaxed);
        let reads = clock.reads.load(Ordering::Relaxed);
        let start = Instant::now();
        for i in 1..=n {
            if name == "compiled_out" {
                black_box(work(i));
            } else {
                turn(&observer, i);
            }
        }
        let elapsed = start.elapsed();
        let allocations = ALLOCS.load(Ordering::Relaxed);
        let reallocations = REALLOCS.load(Ordering::Relaxed);
        let bytes = ALLOCATED.load(Ordering::Relaxed);
        let clock_reads = clock.reads.load(Ordering::Relaxed) - reads;
        let snapshot = observer.metric_snapshot();
        let h = observer.telemetry_health();
        let unfinished = snapshot
            .series
            .iter()
            .filter(|(k, _)| matches!(k.family, MetricFamily::Running | MetricFamily::Unresolved))
            .map(|(_, v)| if let MetricValue::Gauge(v) = v { *v } else { 0 })
            .sum::<u64>();
        println!(
            "{name},{n},{:.1},{:.0},{allocations},{reallocations},{bytes},{clock_reads},1,{unfinished},{},{},{}",
            elapsed.as_nanos() as f64 / n as f64,
            n as f64 / elapsed.as_secs_f64(),
            snapshot.estimated_bytes(),
            h.retained_detail_bytes,
            h.detail_evictions + h.detail_event_drops + h.detail_rejections
        );
    }
}
