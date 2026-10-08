//! Explicit modeling helpers shared by generated and handwritten adapters.
use crate::{Check, ModelError, PropertyId, Rng};

/// Exact macro/runtime expansion protocol. Changing this requires coordinated releases.
pub const MACRO_API_V1: () = ();
/// Content identity of the runtime helpers used by generated adapters.
pub const RUNTIME_BUILD_ID: &str = env!("STATELESS_BUILD_ID");

/// Evaluate details only on failure. Predicate evaluation is owned by the caller.
pub fn check_lazy(
    id: impl Into<PropertyId>,
    passed: bool,
    details: impl FnOnce() -> String,
) -> Check {
    if passed {
        Check::passed(id)
    } else {
        Check::failed(id, details())
    }
}

#[macro_export]
macro_rules! check {
    ($id:expr, $predicate:expr, $($details:tt)+) => {
        $crate::modeling::check_lazy($id, $predicate, || ::std::format!($($details)+))
    };
}

/// Reportable assumptions belong to the application configuration/trace parameters.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DomainDescriptor {
    pub name: &'static str,
    pub assumptions: &'static str,
    pub max_entries: usize,
}

impl DomainDescriptor {
    /// Append under application-owned keys when assembling a trace RunConfig.
    pub fn parameters(&self) -> Vec<(String, String)> {
        vec![
            ("model.domain.name".into(), self.name.into()),
            ("model.domain.assumptions".into(), self.assumptions.into()),
            (
                "model.domain.max_entries".into(),
                self.max_entries.to_string(),
            ),
        ]
    }
}

/// Materialized, ordered environmental deliveries. Duplicates retain their weight.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Domain<I> {
    entries: Vec<I>,
    descriptor: DomainDescriptor,
}
impl<I> Domain<I> {
    pub fn new(descriptor: DomainDescriptor) -> Self {
        Self {
            entries: Vec::new(),
            descriptor,
        }
    }
    pub fn push(&mut self, input: I) -> Result<(), ModelError> {
        if self.entries.len() >= self.descriptor.max_entries {
            return Err(ModelError::new("domain entry limit exceeded"));
        }
        self.entries
            .try_reserve(1)
            .map_err(|_| ModelError::new("domain allocation failed"))?;
        self.entries.push(input);
        Ok(())
    }
    pub fn extend(&mut self, inputs: impl IntoIterator<Item = I>) -> Result<(), ModelError> {
        for input in inputs {
            self.push(input)?;
        }
        Ok(())
    }
    pub fn descriptor(&self) -> &DomainDescriptor {
        &self.descriptor
    }
    pub fn entries(&self) -> &[I] {
        &self.entries
    }
    pub fn into_entries(self) -> Vec<I> {
        self.entries
    }
    pub fn contains(&self, input: &I) -> bool
    where
        I: PartialEq,
    {
        self.entries.contains(input)
    }
    /// Explicit indexed-v1 sampling: one unbiased Rng::index over all entries.
    /// This has a different seed contract from Auto's reservoir algorithm.
    pub fn sample_indexed(&self, rng: &mut Rng) -> Option<&I> {
        rng.index(self.entries.len()).map(|i| &self.entries[i])
    }
    pub fn product<A, B>(
        a: &[A],
        b: &[B],
        maximum: usize,
        mut map: impl FnMut(&A, &B) -> I,
    ) -> Result<Vec<I>, ModelError> {
        let n = a
            .len()
            .checked_mul(b.len())
            .filter(|n| *n <= maximum)
            .ok_or_else(|| ModelError::new("domain product limit exceeded"))?;
        let mut out = Vec::new();
        out.try_reserve_exact(n)
            .map_err(|_| ModelError::new("domain allocation failed"))?;
        for x in a {
            for y in b {
                out.push(map(x, y));
            }
        }
        Ok(out)
    }
}
