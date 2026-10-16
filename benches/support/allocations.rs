//! Requested allocator traffic, measured separately from wall-clock samples.
//! This is not RSS, allocator-reserved memory, or resident/live object size.
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
static ACTIVE: AtomicBool = AtomicBool::new(false);
static ALLOCATIONS: AtomicU64 = AtomicU64::new(0);
static REALLOCATIONS: AtomicU64 = AtomicU64::new(0);
static REQUESTED: AtomicU64 = AtomicU64::new(0);
pub struct MeteredSystem;
// SAFETY: All pointer/layout operations are forwarded unchanged to System.
// Accounting only updates atomics and cannot allocate or dereference pointers.
unsafe impl GlobalAlloc for MeteredSystem {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: Forwarding the allocation request unchanged.
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() && ACTIVE.load(Relaxed) {
            ALLOCATIONS.fetch_add(1, Relaxed);
            REQUESTED.fetch_add(layout.size() as u64, Relaxed);
        }
        pointer
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: Forwarding the allocation request unchanged.
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() && ACTIVE.load(Relaxed) {
            ALLOCATIONS.fetch_add(1, Relaxed);
            REQUESTED.fetch_add(layout.size() as u64, Relaxed);
        }
        pointer
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        // SAFETY: System allocated this pointer with this layout.
        unsafe { System.dealloc(pointer, layout) }
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        // SAFETY: Forwarding the pointer, old layout and new size unchanged.
        let next = unsafe { System.realloc(pointer, layout, size) };
        if !next.is_null() && ACTIVE.load(Relaxed) {
            REALLOCATIONS.fetch_add(1, Relaxed);
            REQUESTED.fetch_add(size as u64, Relaxed);
        }
        next
    }
}
#[derive(Clone, Copy, Debug)]
pub struct Probe {
    pub operations: u64,
    pub allocations: u64,
    pub reallocations: u64,
    pub requested_bytes: u64,
}
impl Probe {
    pub fn json(self) -> String {
        format!(
            "{{\"operations\":{},\"allocations\":{},\"reallocations\":{},\"requested_bytes\":{},\"timed\":false}}",
            self.operations, self.allocations, self.reallocations, self.requested_bytes
        )
    }
}
struct Stop;
impl Drop for Stop {
    fn drop(&mut self) {
        ACTIVE.store(false, Relaxed);
    }
}
pub fn measure(operation: &mut dyn FnMut()) -> Option<Probe> {
    assert!(
        !ACTIVE.swap(true, Relaxed),
        "allocation probes cannot overlap"
    );
    let stop = Stop;
    ALLOCATIONS.store(0, Relaxed);
    REALLOCATIONS.store(0, Relaxed);
    REQUESTED.store(0, Relaxed);
    for _ in 0..32 {
        operation();
    }
    drop(stop);
    Some(Probe {
        operations: 32,
        allocations: ALLOCATIONS.load(Relaxed),
        reallocations: REALLOCATIONS.load(Relaxed),
        requested_bytes: REQUESTED.load(Relaxed),
    })
}
