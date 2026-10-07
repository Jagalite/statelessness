//! Canonical persistence for the campaign self-model's test evidence.
//! This module is test-only: the runtime keeps application reports in the executor.

use super::*;
use crate::ModelCodec;

impl ModelCodec for Lifecycle {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        let mut out = Encoder::default();
        let report = &state.report;
        for count in [
            report.requested,
            report.started,
            report.finished,
            report.not_started,
            report.delivered,
            report.failures,
            report.errors,
        ] {
            out.count(count)?;
        }
        out.number(report.reported_transitions);
        out.number(report.reported_cases);
        out.number(report.reported_skipped_checks);
        out.boolean(report.accounting_complete);
        match &report.termination {
            CampaignTermination::JobsCompleted => out.tag(0),
            CampaignTermination::FailureFound => out.tag(1),
            CampaignTermination::Cancelled => out.tag(2),
            CampaignTermination::Deadline => out.tag(3),
            CampaignTermination::CallbackError(error) => {
                out.tag(4);
                out.error(error)?;
            }
            CampaignTermination::WorkerError(error) => {
                out.tag(5);
                out.error(error)?;
            }
        }
        out.tag(match state.stop {
            None => 0,
            Some(Stop::Failure) => 1,
            Some(Stop::Cancelled) => 2,
            Some(Stop::Deadline) => 3,
            Some(Stop::Sink) => 4,
            Some(Stop::Worker) => 5,
        });
        out.count(state.pending.len())?;
        for (id, pending) in &state.pending {
            out.number(*id);
            match pending {
                Pending::Running => out.tag(0),
                Pending::Ready(summary) => {
                    out.tag(1);
                    out.summary(summary);
                }
            }
        }
        match state.delivering {
            None => out.tag(0),
            Some(id) => {
                out.tag(1);
                out.number(id);
            }
        }
        out.boolean(state.lost_worker);
        out.boolean(state.finalized);
        Ok(out.0)
    }

    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        let mut input = Decoder(bytes);
        let report = CampaignReport {
            requested: input.count()?,
            started: input.count()?,
            finished: input.count()?,
            not_started: input.count()?,
            delivered: input.count()?,
            failures: input.count()?,
            errors: input.count()?,
            reported_transitions: input.number()?,
            reported_cases: input.number()?,
            reported_skipped_checks: input.number()?,
            accounting_complete: input.boolean()?,
            termination: match input.tag()? {
                0 => CampaignTermination::JobsCompleted,
                1 => CampaignTermination::FailureFound,
                2 => CampaignTermination::Cancelled,
                3 => CampaignTermination::Deadline,
                4 => CampaignTermination::CallbackError(input.error()?),
                5 => CampaignTermination::WorkerError(input.error()?),
                _ => return Err(invalid()),
            },
        };
        let stop = match input.tag()? {
            0 => None,
            1 => Some(Stop::Failure),
            2 => Some(Stop::Cancelled),
            3 => Some(Stop::Deadline),
            4 => Some(Stop::Sink),
            5 => Some(Stop::Worker),
            _ => return Err(invalid()),
        };
        let count = input.count()?;
        // Every entry needs an eight-byte ID and one tag. Bound the count before
        // allocating any map nodes, including for hostile length prefixes.
        if count > self.jobs || count > input.0.len() / 9 {
            return Err(invalid());
        }
        let mut pending = BTreeMap::new();
        let mut previous = None;
        for _ in 0..count {
            let id = input.number()?;
            if previous.is_some_and(|previous| id <= previous) {
                return Err(invalid());
            }
            previous = Some(id);
            let entry = match input.tag()? {
                0 => Pending::Running,
                1 => Pending::Ready(input.summary()?),
                _ => return Err(invalid()),
            };
            pending.insert(id, entry);
        }
        let state = State {
            report,
            stop,
            pending,
            delivering: match input.tag()? {
                0 => None,
                1 => Some(input.number()?),
                _ => return Err(invalid()),
            },
            lost_worker: input.boolean()?,
            finalized: input.boolean()?,
        };
        input.finish()?;
        if self.check_state(&state)?.iter().any(Check::is_failure) {
            return Err(ModelError::new(
                "decoded campaign state violates lifecycle properties",
            ));
        }
        Ok(state)
    }

    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        let mut out = Encoder::default();
        match input {
            Input::Claim { stop } => {
                out.tag(0);
                out.tag(match stop {
                    None => 0,
                    Some(ExternalStop::Cancelled) => 1,
                    Some(ExternalStop::Deadline) => 2,
                });
            }
            Input::Complete { job_id, summary } => {
                out.tag(1);
                out.number(*job_id);
                out.summary(summary);
            }
            Input::Receive { job_id } => {
                out.tag(2);
                out.number(*job_id);
            }
            Input::SinkReturned { job_id, error } => {
                out.tag(3);
                out.number(*job_id);
                match error {
                    None => out.tag(0),
                    Some(error) => {
                        out.tag(1);
                        out.error(error)?;
                    }
                }
            }
            Input::WorkerFailed { error, lost_work } => {
                out.tag(4);
                out.error(error)?;
                out.boolean(*lost_work);
            }
            Input::Finish => out.tag(5),
        }
        Ok(out.0)
    }

    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        let mut input = Decoder(bytes);
        let result = match input.tag()? {
            0 => Input::Claim {
                stop: match input.tag()? {
                    0 => None,
                    1 => Some(ExternalStop::Cancelled),
                    2 => Some(ExternalStop::Deadline),
                    _ => return Err(invalid()),
                },
            },
            1 => Input::Complete {
                job_id: input.number()?,
                summary: input.summary()?,
            },
            2 => Input::Receive {
                job_id: input.number()?,
            },
            3 => Input::SinkReturned {
                job_id: input.number()?,
                error: match input.tag()? {
                    0 => None,
                    1 => Some(input.error()?),
                    _ => return Err(invalid()),
                },
            },
            4 => Input::WorkerFailed {
                error: input.error()?,
                lost_work: input.boolean()?,
            },
            5 => Input::Finish,
            _ => return Err(invalid()),
        };
        input.finish()?;
        Ok(result)
    }

    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        let mut out = Encoder::default();
        let (tag, id) = match output {
            Output::RunJob(id) => (0, id),
            Output::Deliver(id) => (1, id),
            Output::Discard(id) => (2, id),
        };
        out.tag(tag);
        out.number(*id);
        Ok(out.0)
    }
}

fn invalid() -> ModelError {
    ModelError::new("invalid campaign lifecycle encoding")
}

#[derive(Default)]
struct Encoder(Vec<u8>);

impl Encoder {
    fn tag(&mut self, value: u8) {
        self.0.push(value);
    }
    fn boolean(&mut self, value: bool) {
        self.tag(u8::from(value));
    }
    fn number(&mut self, value: u64) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }
    fn count(&mut self, value: usize) -> Result<(), ModelError> {
        self.number(u64::try_from(value).map_err(|_| invalid())?);
        Ok(())
    }
    fn error(&mut self, error: &ModelError) -> Result<(), ModelError> {
        self.count(error.0.len())?;
        self.0.extend_from_slice(error.0.as_bytes());
        Ok(())
    }
    fn summary(&mut self, summary: &Summary) {
        self.tag(match summary.outcome {
            Outcome::Completed => 0,
            Outcome::Failure => 1,
            Outcome::Cancelled => 2,
            Outcome::Deadline => 3,
            Outcome::FactoryError => 4,
            Outcome::ModelError => 5,
            Outcome::Panicked => 6,
        });
        self.boolean(summary.failure);
        self.number(summary.transitions);
        self.number(summary.cases);
        self.number(summary.skipped_checks);
    }
}

struct Decoder<'a>(&'a [u8]);

impl<'a> Decoder<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8], ModelError> {
        let result = self.0.get(..count).ok_or_else(invalid)?;
        self.0 = &self.0[count..];
        Ok(result)
    }
    fn tag(&mut self) -> Result<u8, ModelError> {
        Ok(self.take(1)?[0])
    }
    fn boolean(&mut self) -> Result<bool, ModelError> {
        match self.tag()? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(invalid()),
        }
    }
    fn number(&mut self) -> Result<u64, ModelError> {
        let bytes = self.take(8)?.try_into().map_err(|_| invalid())?;
        Ok(u64::from_le_bytes(bytes))
    }
    fn count(&mut self) -> Result<usize, ModelError> {
        usize::try_from(self.number()?).map_err(|_| invalid())
    }
    fn error(&mut self) -> Result<ModelError, ModelError> {
        let count = self.count()?;
        // Validate the entire byte range and UTF-8 before allocating a String.
        let message = std::str::from_utf8(self.take(count)?).map_err(|_| invalid())?;
        Ok(ModelError::new(message))
    }
    fn summary(&mut self) -> Result<Summary, ModelError> {
        Ok(Summary {
            outcome: match self.tag()? {
                0 => Outcome::Completed,
                1 => Outcome::Failure,
                2 => Outcome::Cancelled,
                3 => Outcome::Deadline,
                4 => Outcome::FactoryError,
                5 => Outcome::ModelError,
                6 => Outcome::Panicked,
                _ => return Err(invalid()),
            },
            failure: self.boolean()?,
            transitions: self.number()?,
            cases: self.number()?,
            skipped_checks: self.number()?,
        })
    }
    fn finish(self) -> Result<(), ModelError> {
        if self.0.is_empty() {
            Ok(())
        } else {
            Err(invalid())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn model() -> Lifecycle {
        Lifecycle {
            first_job: 7,
            jobs: 4,
            stop_on_failure: true,
        }
    }

    fn summary(outcome: Outcome) -> Summary {
        let error = matches!(
            outcome,
            Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked
        );
        Summary {
            outcome,
            failure: outcome == Outcome::Failure,
            transitions: if error { 0 } else { u64::MAX },
            cases: if error { 0 } else { 17 },
            skipped_checks: if error { 0 } else { 19 },
        }
    }

    const OUTCOMES: [Outcome; 7] = [
        Outcome::Completed,
        Outcome::Failure,
        Outcome::Cancelled,
        Outcome::Deadline,
        Outcome::FactoryError,
        Outcome::ModelError,
        Outcome::Panicked,
    ];

    #[test]
    fn every_input_roundtrips_and_rejects_truncation_or_trailing_data() {
        let model = model();
        let mut inputs = vec![
            Input::Claim { stop: None },
            Input::Claim {
                stop: Some(ExternalStop::Cancelled),
            },
            Input::Claim {
                stop: Some(ExternalStop::Deadline),
            },
            Input::Receive { job_id: u64::MAX },
            Input::SinkReturned {
                job_id: 7,
                error: None,
            },
            Input::SinkReturned {
                job_id: 0,
                error: Some(ModelError::new("")),
            },
            Input::SinkReturned {
                job_id: 8,
                error: Some(ModelError::new("sink\0錯誤")),
            },
            Input::WorkerFailed {
                error: ModelError::new("worker\nerror"),
                lost_work: false,
            },
            Input::WorkerFailed {
                error: ModelError::new(""),
                lost_work: true,
            },
            Input::Finish,
        ];
        inputs.extend(OUTCOMES.map(|outcome| Input::Complete {
            job_id: 9,
            summary: summary(outcome),
        }));
        for input in inputs {
            let bytes = model.encode_input(&input).unwrap();
            assert_eq!(model.decode_input(&bytes).unwrap(), input);
            for end in 0..bytes.len() {
                assert!(
                    model.decode_input(&bytes[..end]).is_err(),
                    "{input:?}, prefix {end}"
                );
            }
            let mut trailing = bytes;
            trailing.push(0);
            assert!(model.decode_input(&trailing).is_err());
        }
    }

    fn assert_state_roundtrip(model: &Lifecycle, state: &State) {
        let bytes = model.encode_state(state).unwrap();
        let decoded = model.decode_state(&bytes).unwrap();
        assert_eq!(&decoded, state);
        assert_eq!(model.encode_state(&decoded).unwrap(), bytes);
        for end in 0..bytes.len() {
            assert!(
                model.decode_state(&bytes[..end]).is_err(),
                "state prefix {end}"
            );
        }
        let mut trailing = bytes;
        trailing.push(0);
        assert!(model.decode_state(&trailing).is_err());
    }

    #[test]
    fn state_roundtrips_all_reports_stops_pending_and_delivery_fields() {
        let model = model();
        assert_state_roundtrip(&model, &model.initial_state().unwrap());
        let mut state = State {
            report: CampaignReport {
                requested: 4,
                started: 4,
                finished: 3,
                not_started: 0,
                delivered: 2,
                failures: 1,
                errors: 1,
                reported_transitions: u64::MAX,
                reported_cases: 17,
                reported_skipped_checks: 19,
                accounting_complete: false,
                termination: CampaignTermination::JobsCompleted,
            },
            stop: None,
            pending: BTreeMap::from([(10, Pending::Running)]),
            delivering: Some(9),
            lost_worker: false,
            finalized: false,
        };
        assert_state_roundtrip(&model, &state);
        for outcome in OUTCOMES {
            state.pending.insert(10, Pending::Ready(summary(outcome)));
            assert_state_roundtrip(&model, &state);
        }
        for stop in [Stop::Failure, Stop::Cancelled, Stop::Deadline] {
            state.stop = Some(stop);
            assert_state_roundtrip(&model, &state);
        }
        state.delivering = None;
        state.pending.clear();
        state.report.finished = state.report.started;
        state.report.delivered = state.report.finished;
        state.finalized = true;
        for (stop, termination) in [
            (None, CampaignTermination::JobsCompleted),
            (Some(Stop::Failure), CampaignTermination::FailureFound),
            (Some(Stop::Cancelled), CampaignTermination::Cancelled),
            (Some(Stop::Deadline), CampaignTermination::Deadline),
            (
                Some(Stop::Sink),
                CampaignTermination::CallbackError(ModelError::new("sink\0錯誤")),
            ),
            (
                Some(Stop::Worker),
                CampaignTermination::WorkerError(ModelError::new("")),
            ),
        ] {
            state.stop = stop;
            state.report.termination = termination;
            // A callback error requires one receipt whose sink call failed.
            state.report.delivered = state.report.finished
                - usize::from(matches!(
                    state.report.termination,
                    CampaignTermination::CallbackError(_)
                ));
            assert_state_roundtrip(&model, &state);
        }
        state.lost_worker = true;
        state.report.finished -= 1;
        state.report.delivered -= 1;
        state.pending.insert(10, Pending::Running);
        assert_state_roundtrip(&model, &state);
    }

    #[test]
    fn state_rejects_stop_reasons_without_causal_evidence() {
        let enabled = model();
        let disabled = Lifecycle {
            stop_on_failure: false,
            ..model()
        };
        let mut callback_without_failed_delivery = enabled.initial_state().unwrap();
        callback_without_failed_delivery.stop = Some(Stop::Sink);
        callback_without_failed_delivery.report.termination =
            CampaignTermination::CallbackError(ModelError::new("sink was never called"));

        let mut disabled_failure_stop = disabled.initial_state().unwrap();
        disabled_failure_stop.stop = Some(Stop::Failure);
        disabled_failure_stop.report.started = 1;
        disabled_failure_stop.report.not_started -= 1;
        disabled_failure_stop
            .pending
            .insert(7, Pending::Ready(summary(Outcome::Failure)));

        let mut stop_without_failure = enabled.initial_state().unwrap();
        stop_without_failure.stop = Some(Stop::Failure);
        stop_without_failure.report.termination = CampaignTermination::FailureFound;
        stop_without_failure.finalized = true;

        let accepted: Vec<_> = [
            (
                "callback error without a failed sink delivery",
                &enabled,
                callback_without_failed_delivery,
            ),
            (
                "failure stop while its policy is disabled",
                &disabled,
                disabled_failure_stop,
            ),
            (
                "failure termination without an observed failure",
                &enabled,
                stop_without_failure,
            ),
        ]
        .into_iter()
        .filter_map(|(label, model, state)| {
            model
                .decode_state(&model.encode_state(&state).unwrap())
                .is_ok()
                .then_some(label)
        })
        .collect();
        assert!(
            accepted.is_empty(),
            "accepted impossible states: {accepted:?}"
        );
    }

    #[test]
    fn output_tags_and_ids_are_distinct_and_canonical() {
        let model = model();
        for id in [0, 7, u64::MAX] {
            for (tag, output) in [Output::RunJob(id), Output::Deliver(id), Output::Discard(id)]
                .into_iter()
                .enumerate()
            {
                let mut expected = vec![tag as u8];
                expected.extend_from_slice(&id.to_le_bytes());
                assert_eq!(model.encode_output(&output).unwrap(), expected);
            }
        }
    }

    #[test]
    fn input_rejects_unknown_tags_flags_lengths_and_invalid_utf8() {
        let model = model();
        assert!(model.decode_input(&[255]).is_err());
        assert!(model.decode_input(&[0, 3]).is_err());
        let complete = model
            .encode_input(&Input::Complete {
                job_id: 7,
                summary: summary(Outcome::Completed),
            })
            .unwrap();
        for index in [9, 10] {
            let mut bytes = complete.clone();
            bytes[index] = 255;
            assert!(model.decode_input(&bytes).is_err());
        }
        let mut sink = model
            .encode_input(&Input::SinkReturned {
                job_id: 7,
                error: None,
            })
            .unwrap();
        sink[9] = 2;
        assert!(model.decode_input(&sink).is_err());
        let worker = model
            .encode_input(&Input::WorkerFailed {
                error: ModelError::new("x"),
                lost_work: false,
            })
            .unwrap();
        let mut bytes = worker.clone();
        bytes[1..9].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(model.decode_input(&bytes).is_err());
        let mut bytes = worker.clone();
        bytes[9] = 255;
        assert!(model.decode_input(&bytes).is_err());
        let mut bytes = worker;
        *bytes.last_mut().unwrap() = 2;
        assert!(model.decode_input(&bytes).is_err());
    }

    #[test]
    fn state_rejects_bad_tags_counts_order_duplicates_and_invalid_properties() {
        let model = model();
        let initial = model.initial_state().unwrap();
        let initial_bytes = model.encode_state(&initial).unwrap();
        // Ten u64 report fields precede the accounting flag, termination, and stop.
        for index in [80, 81, 82, 91, 92, 93] {
            let mut bytes = initial_bytes.clone();
            bytes[index] = 255;
            assert!(model.decode_state(&bytes).is_err(), "tag at {index}");
        }
        let mut bytes = initial_bytes;
        bytes[83..91].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(model.decode_state(&bytes).is_err());
        let claimed = model
            .step(&initial, &Input::Claim { stop: None })
            .unwrap()
            .state;
        let twice = model
            .step(&claimed, &Input::Claim { stop: None })
            .unwrap()
            .state;
        let bytes = model.encode_state(&twice).unwrap();
        let mut duplicate = bytes.clone();
        duplicate[100..108].copy_from_slice(&7_u64.to_le_bytes());
        assert!(model.decode_state(&duplicate).is_err());
        let mut reversed = bytes.clone();
        reversed[91..99].copy_from_slice(&8_u64.to_le_bytes());
        reversed[100..108].copy_from_slice(&7_u64.to_le_bytes());
        assert!(model.decode_state(&reversed).is_err());
        let mut bad_pending = bytes;
        bad_pending[99] = 2;
        assert!(model.decode_state(&bad_pending).is_err());
        let mut invalid_state = initial;
        invalid_state.report.started = 5;
        assert!(
            model
                .decode_state(&model.encode_state(&invalid_state).unwrap())
                .is_err()
        );
        let mut invalid_summary = claimed.clone();
        let mut inconsistent = summary(Outcome::FactoryError);
        inconsistent.transitions = 1;
        invalid_summary
            .pending
            .insert(7, Pending::Ready(inconsistent));
        assert!(
            model
                .decode_state(&model.encode_state(&invalid_summary).unwrap())
                .is_err()
        );
        let mut inconsistent_reason = model.initial_state().unwrap();
        inconsistent_reason.finalized = true;
        inconsistent_reason.report.termination = CampaignTermination::Cancelled;
        assert!(
            model
                .decode_state(&model.encode_state(&inconsistent_reason).unwrap())
                .is_err()
        );
        let mut inconsistent_loss = model.initial_state().unwrap();
        inconsistent_loss.lost_worker = true;
        inconsistent_loss.stop = Some(Stop::Worker);
        inconsistent_loss.report.termination =
            CampaignTermination::WorkerError(ModelError::new("lost"));
        assert!(
            model
                .decode_state(&model.encode_state(&inconsistent_loss).unwrap())
                .is_err()
        );
        let other_range = Lifecycle {
            first_job: 70,
            jobs: 4,
            stop_on_failure: true,
        };
        assert!(
            other_range
                .decode_state(&model.encode_state(&claimed).unwrap())
                .is_err()
        );
    }
}
