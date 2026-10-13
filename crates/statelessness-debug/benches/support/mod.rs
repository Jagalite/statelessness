//! Shared sampling and raw evidence for the initial reproducible benchmark set.
use std::time::Instant;

#[allow(dead_code)]
pub mod allocations;

#[derive(Clone, Copy)]
pub struct Config {
    pub iterations: usize,
    samples: usize,
    warmup: usize,
    json: bool,
}

impl Config {
    pub fn from_env() -> Self {
        fn positive(name: &str, fallback: usize) -> usize {
            match std::env::var(name) {
                Ok(value) => value
                    .parse::<usize>()
                    .ok()
                    .filter(|value| *value > 0)
                    .unwrap_or_else(|| panic!("{name} must be a positive integer")),
                Err(std::env::VarError::NotPresent) => fallback,
                Err(error) => panic!("{name}: {error}"),
            }
        }
        let samples = positive("STATELESS_BENCH_SAMPLES", 7);
        assert!(samples >= 2, "STATELESS_BENCH_SAMPLES must be at least two");
        let format = std::env::var("STATELESS_BENCH_FORMAT").unwrap_or_default();
        assert!(
            matches!(format.as_str(), "" | "text" | "jsonl"),
            "STATELESS_BENCH_FORMAT must be text or jsonl"
        );
        Self {
            iterations: positive("STATELESS_BENCH_ITERS", 20_000),
            samples,
            warmup: positive("STATELESS_BENCH_WARMUP", 100),
            json: format == "jsonl",
        }
    }

    #[allow(dead_code)]
    pub fn measure(
        self,
        suite: &str,
        case: &str,
        units: usize,
        unit: &str,
        mut operation: impl FnMut(),
    ) {
        self.measure_detailed(suite, case, units, unit, &[], &mut operation, |_| None);
    }

    #[allow(clippy::too_many_arguments)]
    pub fn measure_detailed(
        self,
        suite: &str,
        case: &str,
        units: usize,
        unit: &str,
        workload: &[(&str, u64)],
        mut operation: impl FnMut(),
        mut allocation_probe: impl FnMut(&mut dyn FnMut()) -> Option<allocations::Probe>,
    ) {
        // These identifiers are source constants, not arbitrary user input.
        for identifier in [suite, case, unit] {
            assert!(
                identifier
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_')
            );
        }
        assert!(units > 0);
        for _ in 0..self.warmup {
            operation();
        }
        let mut elapsed_ns = Vec::with_capacity(self.samples);
        for _ in 0..self.samples {
            let start = Instant::now();
            for _ in 0..self.iterations {
                operation();
            }
            elapsed_ns.push(start.elapsed().as_nanos());
        }
        // Instrumentation runs in a separate pass after every timed sample.
        let allocation = allocation_probe(&mut operation);
        let allocation_json = allocation.map_or_else(|| "null".into(), |p| p.json());
        let workload_json = workload
            .iter()
            .map(|(name, value)| {
                assert!(name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'));
                format!("\"{name}\":{value}")
            })
            .collect::<Vec<_>>()
            .join(",");
        if self.json {
            println!(
                "{{\"schema_version\":1,\"suite\":\"{suite}\",\"case\":\"{case}\",\"unit\":\"{unit}\",\"units_per_operation\":{units},\"iterations_per_sample\":{},\"warmup_iterations\":{},\"elapsed_ns\":{elapsed_ns:?},\"workload\":{{{workload_json}}},\"allocation_probe\":{allocation_json}}}",
                self.iterations, self.warmup
            );
        } else {
            let mut samples: Vec<_> = elapsed_ns
                .iter()
                .map(|elapsed| *elapsed as f64 / self.iterations as f64)
                .collect();
            samples.sort_by(f64::total_cmp);
            let middle = samples.len() / 2;
            let median = if samples.len().is_multiple_of(2) {
                (samples[middle - 1] + samples[middle]) / 2.0
            } else {
                samples[middle]
            };
            println!(
                "{case}: median {median:.1} ns/op; sample range {:.1}..{:.1} ns/op; {} samples",
                samples[0],
                samples[samples.len() - 1],
                samples.len()
            );
        }
    }
}
