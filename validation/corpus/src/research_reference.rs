//! Real public finite-research reference. JSON adapts table models and reports;
//! no traversal, quotient, pair comparison or witness algorithm is duplicated.
use serde_json::{Value, json};
use stateless::{
    assurance::{PropertyPhase, RequiredProperty},
    model::*,
    research::*,
};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    io::{Read, Write},
    path::Path,
};
type R<T> = Result<T, ModelError>;
fn error(message: impl Into<String>) -> ModelError {
    ModelError::new(message)
}
fn text<'a>(v: &'a Value, key: &str) -> R<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| error(format!("missing string {key}")))
}
fn uint(v: &Value) -> R<u64> {
    match v {
        Value::String(s) => {
            let n = s.parse::<u64>().map_err(|_| error("unsigned decimal"))?;
            if n.to_string() != *s {
                return Err(error("noncanonical unsigned decimal"));
            }
            Ok(n)
        }
        _ => v.as_u64().ok_or_else(|| error("unsigned integer")),
    }
}
fn u32_value(v: &Value) -> R<u32> {
    u32::try_from(uint(v)?).map_err(|_| error("u32 overflow"))
}
fn array<'a>(v: &'a Value, key: &str) -> R<&'a [Value]> {
    v[key]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| error(format!("missing array {key}")))
}
fn boolean(v: &Value, key: &str) -> R<bool> {
    v[key]
        .as_bool()
        .ok_or_else(|| error(format!("missing boolean {key}")))
}
fn patch(base: &Value, changes: &Value) -> Value {
    let mut value = base.clone();
    if let (Some(target), Some(changes)) = (value.as_object_mut(), changes.as_object()) {
        for (k, v) in changes {
            let next = target
                .get(k)
                .map(|old| patch(old, v))
                .unwrap_or_else(|| v.clone());
            target.insert(k.clone(), next);
        }
    } else if !changes.is_null() {
        value = changes.clone();
    }
    value
}
fn metadata(v: &Value) -> R<ModelMetadata> {
    Ok(ModelMetadata {
        name: text(v, "name")?.into(),
        model_version: u32_value(&v["model_version"])?,
        properties_version: u32_value(&v["properties_version"])?,
        codec_version: u32_value(&v["codec_version"])?,
        build: text(v, "build")?.into(),
    })
}
fn property_phase(v: &str) -> R<PropertyPhase> {
    match v {
        "state" => Ok(PropertyPhase::State),
        "transition" => Ok(PropertyPhase::Transition),
        _ => Err(error("property phase")),
    }
}
fn property(v: &Value) -> R<RequiredProperty> {
    Ok(RequiredProperty {
        id: text(v, "id")?.into(),
        phase: property_phase(text(v, "phase")?)?,
    })
}
fn assumptions(v: &Value) -> R<Assumptions> {
    Ok(Assumptions {
        established: boolean(v, "established")?,
        finite_domain: text(v, "finite_domain")?.into(),
        equivalence: text(v, "equivalence")?.into(),
        effects: text(v, "effects")?.into(),
        transition_properties: text(v, "transition_properties")?.into(),
        queue_order: text(v, "queue_order")?.into(),
        oracle_history: text(v, "oracle_history")?.into(),
        visible_timing: text(v, "visible_timing")?.into(),
    })
}
fn proposal(v: &Value) -> R<Proposal> {
    Ok(Proposal {
        name: text(v, "name")?.into(),
        version: u32_value(&v["version"])?,
        build: text(v, "build")?.into(),
        assumptions: assumptions(&v["assumptions"])?,
        required_properties: array(v, "required_properties")?
            .iter()
            .map(property)
            .collect::<R<_>>()?,
    })
}
fn config(v: &Value) -> R<ExperimentConfig> {
    Ok(ExperimentConfig {
        enabled: boolean(v, "enabled")?,
        max_states: usize::try_from(uint(&v["max_states"])?)
            .map_err(|_| error("usize overflow"))?,
        max_edges: uint(&v["max_edges"])?,
        max_equivalence_pairs: uint(&v["max_equivalence_pairs"])?,
    })
}
fn checks(values: &[Value]) -> R<Vec<Check>> {
    values
        .iter()
        .map(|v| {
            let id = text(v, "id")?.to_owned();
            Ok(match text(v, "status")? {
                "passed" => Check::passed(id),
                "failed" => Check::failed(id, text(v, "details")?),
                "skipped" => Check::skipped(id, text(v, "details")?),
                _ => return Err(error("check status")),
            })
        })
        .collect()
}
#[derive(Clone)]
struct Edge {
    input: String,
    target: u32,
    outputs: Vec<String>,
    disposition: Disposition,
    checks: Vec<Check>,
}
struct Row {
    checks: Vec<Check>,
    inputs: Vec<Edge>,
}
struct TableModel {
    metadata: ModelMetadata,
    initial: u32,
    rows: BTreeMap<u32, Row>,
    states: Vec<u32>,
    keys: Vec<String>,
    error_at: Option<String>,
    callback_log: RefCell<Vec<String>>,
}
impl TableModel {
    fn from_json(meta: &Value, scenario: &Value, error_at: &Value) -> R<Self> {
        let mut rows = BTreeMap::new();
        for row in array(scenario, "rows")? {
            let state = u32_value(&row["state"])?;
            let mut inputs = vec![];
            for edge in array(row, "inputs")? {
                let disposition = match text(&edge["disposition"], "kind")? {
                    "accepted" => Disposition::Accepted,
                    "rejected" => {
                        Disposition::Rejected(text(&edge["disposition"], "reason")?.into())
                    }
                    "ignored" => Disposition::Ignored(text(&edge["disposition"], "reason")?.into()),
                    _ => return Err(error("disposition")),
                };
                inputs.push(Edge {
                    input: text(edge, "input")?.into(),
                    target: u32_value(&edge["target"])?,
                    outputs: array(edge, "outputs")?
                        .iter()
                        .map(|v| {
                            v.as_str()
                                .map(ToOwned::to_owned)
                                .ok_or_else(|| error("output string"))
                        })
                        .collect::<R<_>>()?,
                    disposition,
                    checks: checks(array(edge, "checks")?)?,
                });
            }
            if rows
                .insert(
                    state,
                    Row {
                        checks: checks(array(row, "checks")?)?,
                        inputs,
                    },
                )
                .is_some()
            {
                return Err(error("duplicate fixture row"));
            }
        }
        let states = array(scenario, "states")?
            .iter()
            .map(u32_value)
            .collect::<R<Vec<_>>>()?;
        let keys = array(scenario, "keys")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(ToOwned::to_owned)
                    .ok_or_else(|| error("projection key string"))
            })
            .collect::<R<Vec<_>>>()?;
        if states.len() != keys.len() {
            return Err(error("fixture keys must parallel states"));
        }
        Ok(Self {
            metadata: metadata(meta)?,
            initial: u32_value(&scenario["initial"])?,
            rows,
            states,
            keys,
            error_at: error_at.as_str().map(ToOwned::to_owned),
            callback_log: RefCell::new(vec![]),
        })
    }
    fn event(&self, event: String) -> R<()> {
        self.callback_log.borrow_mut().push(event.clone());
        if self.error_at.as_deref() == Some(&event) {
            Err(error(format!("fixture error at {event}")))
        } else {
            Ok(())
        }
    }
    fn row(&self, state: u32) -> R<&Row> {
        self.rows
            .get(&state)
            .ok_or_else(|| error("missing fixture state row"))
    }
    fn edge(&self, state: u32, input: &str) -> R<&Edge> {
        self.row(state)?
            .inputs
            .iter()
            .find(|e| e.input == input)
            .ok_or_else(|| error("missing fixture input edge"))
    }
    fn project(&self, state: &u32) -> String {
        self.callback_log
            .borrow_mut()
            .push(format!("projection:{state}"));
        let index = self
            .states
            .iter()
            .position(|s| s == state)
            .expect("projection input is declared state");
        self.keys[index].clone()
    }
}
impl Model for TableModel {
    type State = u32;
    type Input = String;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        self.callback_log.borrow_mut().push("metadata".into());
        self.metadata.clone()
    }
    fn initial_state(&self) -> R<u32> {
        self.event("initial".into())?;
        Ok(self.initial)
    }
    fn step(&self, state: &u32, input: &String) -> R<Transition<u32, String>> {
        self.event(format!("step:{state}:{input}"))?;
        let e = self.edge(*state, input)?;
        Ok(Transition {
            state: e.target,
            outputs: e.outputs.clone(),
            disposition: e.disposition.clone(),
        })
    }
    fn check_state(&self, _: &u32) -> R<Vec<Check>> {
        Err(error("legacy fixture state checker called"))
    }
    fn check_state_into(&self, state: &u32, sink: &mut CheckSink<'_>) -> R<()> {
        self.event(format!("state:{state}"))?;
        for c in &self.row(*state)?.checks {
            sink.push(c.clone());
        }
        Ok(())
    }
    fn check_transition(
        &self,
        _: &u32,
        _: &String,
        _: &TransitionRef<'_, u32, String>,
    ) -> R<Vec<Check>> {
        Err(error("legacy fixture transition checker called"))
    }
    fn check_transition_into(
        &self,
        before: &u32,
        input: &String,
        _: &TransitionRef<'_, u32, String>,
        sink: &mut CheckSink<'_>,
    ) -> R<()> {
        self.event(format!("transition:{before}:{input}"))?;
        for c in &self.edge(*before, input)?.checks {
            sink.push(c.clone());
        }
        Ok(())
    }
}
struct Inputs<'a> {
    model: &'a TableModel,
    state: u32,
    index: usize,
}
impl Iterator for Inputs<'_> {
    type Item = String;
    fn next(&mut self) -> Option<String> {
        let index = self.index;
        self.index += 1;
        self.model
            .callback_log
            .borrow_mut()
            .push(format!("next:{}:{index}", self.state));
        self.model
            .rows
            .get(&self.state)?
            .inputs
            .get(index)
            .map(|e| e.input.clone())
    }
}
impl Enumerate for TableModel {
    fn inputs(&self, _: &u32) -> R<Vec<String>> {
        Err(error("allocating fixture enumeration called"))
    }
    fn input_iter<'a>(&'a self, state: &'a u32) -> R<Box<dyn Iterator<Item = String> + 'a>> {
        self.event(format!("inputs:{state}"))?;
        self.row(*state)?;
        Ok(Box::new(Inputs {
            model: self,
            state: *state,
            index: 0,
        }))
    }
}

fn property_json(p: &RequiredProperty) -> Value {
    json!({"id":p.id,"phase":match p.phase {PropertyPhase::State=>"state",PropertyPhase::Transition=>"transition"}})
}
fn witness_json(w: &ViolationWitness<String>) -> Value {
    json!({"property":property_json(&w.property),"details":w.details,"inputs":w.inputs})
}
fn reachability_json(r: &Reachability<String>) -> Value {
    json!({"states":r.states,"edges":r.edges.to_string(),
        "violations":r.violations.iter().map(witness_json).collect::<Vec<_>>(),
        "observed_properties":r.observed_properties.iter().map(property_json).collect::<Vec<_>>(),
        "skipped_checks":r.skipped_checks.to_string()})
}
fn report_json(r: &ExperimentReport<String>) -> Value {
    let a = &r.proposal.assumptions;
    json!({"model":{"name":r.model.name,"model_version":r.model.model_version,"properties_version":r.model.properties_version,"codec_version":r.model.codec_version,"build":r.model.build},
    "proposal":{"name":r.proposal.name,"version":r.proposal.version,"build":r.proposal.build,
        "assumptions":{"established":a.established,"finite_domain":a.finite_domain,"equivalence":a.equivalence,"effects":a.effects,"transition_properties":a.transition_properties,"queue_order":a.queue_order,"oracle_history":a.oracle_history,"visible_timing":a.visible_timing},
        "required_properties":r.proposal.required_properties.iter().map(property_json).collect::<Vec<_>>()},
    "config":{"enabled":r.config.enabled,"max_states":r.config.max_states.to_string(),"max_edges":r.config.max_edges.to_string(),"max_equivalence_pairs":r.config.max_equivalence_pairs.to_string()},
    "status":match r.status {ExperimentStatus::Disabled=>"Disabled",ExperimentStatus::AssumptionsNotEstablished=>"AssumptionsNotEstablished",ExperimentStatus::Incomplete=>"Incomplete",ExperimentStatus::Counterexample=>"Counterexample",ExperimentStatus::AgreesWithinDeclaredFixture=>"AgreesWithinDeclaredFixture"},
    "declared_states":r.declared_states.to_string(),"materialized_edges":r.materialized_edges.to_string(),"compared_pairs":r.compared_pairs.to_string(),
    "exact":r.exact.as_ref().map(reachability_json),"reduced":r.reduced.as_ref().map(reachability_json),
    "counterexamples":r.counterexamples.iter().map(|c|json!({"left_state":c.left_state,"right_state":c.right_state,"left_prefix":c.left_prefix,"right_prefix":c.right_prefix,"reason":c.reason})).collect::<Vec<_>>(),
    "missing_violations":r.missing_violations.iter().map(witness_json).collect::<Vec<_>>(),
    "missing_properties":r.missing_properties.iter().map(property_json).collect::<Vec<_>>(),"limitation":r.limitation})
}
fn execute(defaults: &Value, case: &Value) -> Result<Value, String> {
    let model = TableModel::from_json(&defaults["metadata"], &case["scenario"], &case["error_at"])
        .map_err(|e| e.to_string())?;
    let proposal = proposal(&patch(&defaults["proposal"], &case["proposal_overrides"]))
        .map_err(|e| e.to_string())?;
    let config = config(&patch(&defaults["config"], &case["config_overrides"]))
        .map_err(|e| e.to_string())?;
    let result = compare_equivalence(&model, &model.states, proposal, config, |state| {
        model.project(state)
    });
    let (error, report, qualification) = match result {
        Ok(report) => (
            None,
            report_json(&report),
            json!(qualification_parameters(&report)),
        ),
        Err(error) => (Some(error.to_string()), Value::Null, Value::Null),
    };
    Ok(
        json!({"error":error,"report":report,"callback_log":*model.callback_log.borrow(),"qualification_parameters":qualification}),
    )
}
fn read_corpus(path: &Path) -> Result<Value, String> {
    let mut bytes = vec![];
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
fn results(corpus: &Value, check: bool) -> Result<Vec<Value>, String> {
    let cases = corpus["cases"].as_array().ok_or("missing cases")?;
    let mut results = vec![];
    for case in cases {
        let id = case["case"].as_str().ok_or("missing case id")?;
        let mut actual = execute(&corpus["defaults"], case)
            .map_err(|e| format!("{id}: fixture adapter: {e}"))?;
        if check && actual != case["expected"] {
            return Err(format!(
                "{id}: actual {actual} differs from expected {}",
                case["expected"]
            ));
        }
        actual
            .as_object_mut()
            .expect("normalized result object")
            .insert("case".into(), json!(id));
        results.push(actual);
    }
    Ok(results)
}
pub fn qualify(path: &Path) -> Result<Value, String> {
    let corpus = read_corpus(path)?;
    let results = results(&corpus, true)?;
    let native_exclusions=corpus["native_admission_cases"].as_array().ok_or("missing native_admission_cases")?.iter().map(|row|json!({"case":row["case"],"classification":"static_exclusion","reason":"Rust fixture uses typed unsigned arguments, exact Eq String keys and synchronous infallible metadata/projection/iterator-next signatures; these host-negative cases are not shared Rust Result invocations."})).collect::<Vec<_>>();
    Ok(
        json!({"producer":"rust","mode":"qualify","profile":"research-equivalence-v1","case_count":results.len(),"native_only_not_executed":native_exclusions.len(),"native_exclusions":native_exclusions,"production_pruning_enabled":false}),
    )
}
pub fn run(path: &Path, output: &Path) -> Result<Value, String> {
    let corpus = read_corpus(path)?;
    let results = results(&corpus, false)?;
    let count = results.len();
    let envelope = json!({"profile":"research-equivalence-v1","results":results});
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|e| e.to_string())?;
    file.write_all(&serde_json::to_vec(&envelope).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    Ok(
        json!({"producer":"rust","mode":"run","case_count":count,"production_pruning_enabled":false}),
    )
}
