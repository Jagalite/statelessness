//! Reproducible microbenchmark; timings are observations, never pass/fail gates.
use macro_qualification::Counter;
use stateless::*;
use std::{
    alloc::{GlobalAlloc, Layout, System},
    hint::black_box,
    sync::atomic::{AtomicUsize, Ordering},
    time::Instant,
};
struct Counting;
static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);
// SAFETY: This allocator forwards every operation with its original layout and
// pointer to System. The atomic counter observes allocation calls only.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.alloc(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) }
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.realloc(p, l, n) }
    }
}
#[global_allocator]
static ALLOCATOR: Counting = Counting;
struct Hand;
impl Model for Hand {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        Counter.metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &u8, i: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(s.saturating_add(*i), vec![*i]))
    }
    fn check_state(&self, s: &u8) -> Result<Vec<Check>, ModelError> {
        let mut c = vec![];
        self.check_state_into(s, &mut CheckSink::new(&mut c))?;
        Ok(c)
    }
    fn check_state_into(&self, s: &u8, c: &mut CheckSink<'_>) -> Result<(), ModelError> {
        c.push(if *s <= 3 {
            Check::passed("counter.bound")
        } else {
            Check::failed("counter.bound", "overflow")
        });
        Ok(())
    }
}
fn timed(name: &str, mut f: impl FnMut() -> u64) {
    let start = Instant::now();
    ALLOCATIONS.store(0, Ordering::Relaxed);
    let checksum = f();
    let allocations = ALLOCATIONS.load(Ordering::Relaxed);
    let ns = start.elapsed().as_nanos();
    println!("{name},{ns},{allocations},{checksum}");
}
fn steps<M: Model<State = u8, Input = u8, Output = u8>>(m: &M) -> u64 {
    let mut sum = 0;
    for i in 0..500_000 {
        sum +=
            u64::from(black_box(m.step(&black_box((i % 128) as u8), &black_box(1)).unwrap()).state);
    }
    sum
}
fn checks<M: Model<State = u8>>(m: &M) -> u64 {
    let mut checks = Vec::with_capacity(1);
    let mut sum = 0;
    for i in 0..500_000 {
        checks.clear();
        m.check_state_into(&black_box((i % 4) as u8), &mut CheckSink::new(&mut checks))
            .unwrap();
        sum += black_box(checks.len()) as u64;
    }
    sum
}
fn main() {
    println!("case,nanoseconds,allocation_calls,checksum");
    for _ in 0..3 {
        timed("hand_steps", || steps(&Hand));
        timed("macro_steps", || steps(&Counter));
        timed("hand_reused_checks", || checks(&Hand));
        timed("macro_reused_checks", || checks(&Counter));
    }
    for n in [16, 256, 4096] {
        let descriptor = stateless::modeling::DomainDescriptor {
            name: "bench",
            assumptions: "dense integers",
            max_entries: n,
        };
        let mut domain = stateless::modeling::Domain::new(descriptor);
        timed(&format!("materialize_{n}"), || {
            domain.extend(0..n).unwrap();
            domain.entries().len() as u64
        });
        timed(&format!("indexed_{n}_1000"), || {
            let mut rng = Rng::new(42);
            (0..1000)
                .map(|_| *black_box(domain.sample_indexed(&mut rng).unwrap()) as u64)
                .sum()
        });
        timed(&format!("reservoir_{n}_1000"), || {
            let mut rng = Rng::new(42);
            let mut sum = 0;
            for _ in 0..1000 {
                let mut selected = 0;
                for i in 0..n {
                    if rng.index(i + 1) == Some(0) {
                        selected = i;
                    }
                }
                sum += black_box(selected) as u64;
            }
            sum
        });
    }
}
