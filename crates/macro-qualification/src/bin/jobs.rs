use macro_qualification::jobs::{Adapter, Input};
use stateless::{Model, execution, trace};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let model = Adapter { omit_cleanup: true };
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["record", path] => {
            let trace = execution::record(
                &model,
                vec![Input::Submit, Input::Cancel(1), Input::Complete(1)],
                trace::RunConfig {
                    parameters: macro_qualification::jobs::domain(&model, &model.initial_state()?)?
                        .descriptor()
                        .parameters(),
                    ..Default::default()
                },
                10,
            )?;
            trace.write_to(std::fs::File::create_new(path)?)?;
        }
        ["replay", path] => {
            let trace = trace::Trace::read_from(std::fs::File::open(path)?, &Default::default())?;
            let report = execution::replay(&model, &trace, Default::default())?;
            assert_eq!(report.outcome, execution::ReplayOutcome::Exact);
            assert!(report.failure_reproduced);
            println!("Exact; missing cleanup reproduced");
        }
        _ => return Err("jobs record NEW_TRACE | replay TRACE".into()),
    }
    Ok(())
}
