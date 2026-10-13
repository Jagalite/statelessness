//! Separates borrowed observation/formatting from buffered file I/O.
use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::execution::{CheckPolicy, check_observed};
use stateless::observation::*;
use std::hint::black_box;
use std::io::{self, Write};
use std::num::NonZeroU64;

mod support;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = support::Config::from_env();
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
    eprintln!(
        "Small request fixture; {} iterations/sample; build {}",
        config.iterations,
        model.metadata().build
    );
    config.measure("observation", "borrowed_event", 1, "event", || {
        black_box(event());
    });
    for (level, case) in [
        (LogLevel::Off, "off_to_sink"),
        (LogLevel::Info, "info_to_sink"),
        (LogLevel::Debug, "debug_to_sink"),
        (LogLevel::Trace, "trace_to_sink"),
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
        config.measure("observation", case, 1, "event", || {
            logger
                .observe(black_box(&model), black_box(event()))
                .unwrap();
            black_box(&logger);
        });
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
        config.measure("observation", "buffered_file_no_fsync", 1, "record", || {
            writer.write_all(black_box(&bytes)).unwrap();
        });
        writer.flush()?;
        Ok(())
    })();
    std::fs::remove_file(&path)?;
    result?;
    eprintln!(
        "{} bytes/record. File timing includes OS caching, not durability. No production or end-to-end performance claim.",
        bytes.len()
    );
    Ok(())
}
