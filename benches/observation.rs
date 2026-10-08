//! Separates borrowed observation/formatting from buffered file I/O.
use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::execution::{CheckPolicy, check_observed};
use stateless::observation::*;
use std::hint::black_box;
use std::io::{self, Write};
use std::num::NonZeroU64;
use std::time::Instant;

fn measure(name: &str, iterations: usize, mut f: impl FnMut()) {
    for _ in 0..100 {
        f();
    }
    let mut samples = Vec::new();
    for _ in 0..7 {
        let start = Instant::now();
        for _ in 0..iterations {
            f();
        }
        samples.push(start.elapsed().as_nanos() as f64 / iterations as f64);
    }
    samples.sort_by(f64::total_cmp);
    println!(
        "{name}: median {:.1} ns/op (range {:.1}..{:.1})",
        samples[3], samples[0], samples[6]
    );
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let iterations = std::env::var("STATELESS_BENCH_ITERS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(20_000)
        .max(1);
    let model = RequestModel::fixed();
    let before = model.initial_state()?;
    let input = Input::Start;
    let transition = model.step(&before, &input)?;
    let checks = check_observed(
        &model,
        &before,
        &input,
        &transition,
        1,
        CheckPolicy::default(),
    )?;
    let event = || Event::Transition {
        sequence: NonZeroU64::new(1).unwrap(),
        before: &before,
        input: &input,
        transition: transition.as_ref(),
        checks: &checks,
    };
    println!(
        "Small request fixture; {iterations} iterations/sample; 7 samples; build {}",
        model.metadata().build
    );
    measure("borrowed event baseline", iterations, || {
        black_box(event());
    });
    for level in [
        LogLevel::Off,
        LogLevel::Info,
        LogLevel::Debug,
        LogLevel::Trace,
    ] {
        let mut logger = TextObserver::new(
            io::sink(),
            "bench",
            ObservationOptions {
                level,
                snapshots: SnapshotPolicy::EveryTransition,
                ..ObservationOptions::default()
            },
            EncodedPayloads::new(1024),
        );
        measure(
            &format!("{level:?} observation to sink"),
            iterations,
            || {
                logger
                    .observe(black_box(&model), black_box(event()))
                    .unwrap();
                black_box(&logger);
            },
        );
    }
    let mut logger = TextObserver::new(
        Vec::new(),
        "bench",
        ObservationOptions {
            level: LogLevel::Trace,
            snapshots: SnapshotPolicy::EveryTransition,
            ..ObservationOptions::default()
        },
        EncodedPayloads::new(1024),
    );
    logger.observe(&model, event())?;
    let bytes = logger.into_inner();
    // Create a unique file exclusively. This benchmark removes only that file.
    let path = std::env::temp_dir().join(format!(
        "stateless-observation-{}-{}.log",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    let result = (|| -> io::Result<()> {
        let mut writer = io::BufWriter::new(file);
        measure(
            "preformatted buffered file write (no fsync)",
            iterations,
            || {
                writer.write_all(black_box(&bytes)).unwrap();
            },
        );
        writer.flush()?;
        Ok(())
    })();
    std::fs::remove_file(&path)?;
    result?;
    println!(
        "{} bytes/record. File timing includes OS caching, not durability. No production or end-to-end performance claim.",
        bytes.len()
    );
    Ok(())
}
