//! Versioned, read-only display projections. These are never replay/equality keys.
//!
//! Implementations must be side-effect-free and honor the context's bounds. Limits
//! bound debugger-owned projection payloads, not allocations inside user callbacks.
use std::collections::BTreeMap;
use std::fmt;

pub const INSPECT_API_V1: () = ();
pub const DISPLAY_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
pub struct SnapshotId {
    pub session: u64,
    pub revision: u64,
    pub sequence: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SourceSite {
    pub file: &'static str,
    pub line: u32,
    pub column: u32,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DisplaySchema {
    pub name: &'static str,
    pub version: u32,
    pub source: Option<SourceSite>,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum IntegerType {
    I8,
    I16,
    I32,
    I64,
    I128,
    Isize,
    U8,
    U16,
    U32,
    U64,
    U128,
    Usize,
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum MapKey {
    String(String),
    Integer { kind: IntegerType, decimal: String },
    Bool(bool),
    Char(char),
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum PathSegment {
    Field(String),
    Variant(String),
    Index(usize),
    MapKey(MapKey),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Scalar {
    Unit,
    Bool(bool),
    Char(char),
    Integer {
        kind: IntegerType,
        decimal: String,
    },
    Float {
        kind: &'static str,
        decimal: String,
        bits: u64,
    },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IncompleteReason {
    Pagination,
    Sampled,
    ObservationGap,
    SchemaChanged,
    Work,
    Depth,
    Nodes,
    Bytes,
    ValueLimit,
    Redacted,
    Opaque,
    Unavailable,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Completeness {
    Complete,
    Partial(IncompleteReason),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MapOrdering {
    Stable,
    Unstable,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NodeKind {
    Scalar(Scalar),
    String {
        preview: String,
        total_bytes: usize,
    },
    Bytes {
        preview: Vec<u8>,
        total_bytes: usize,
    },
    Object {
        name: String,
    },
    Enum {
        name: String,
        variant_id: String,
        variant_label: String,
    },
    Sequence {
        name: String,
    },
    Map {
        ordering: MapOrdering,
    },
    Opaque {
        label: String,
    },
    Redacted,
    Unavailable,
    Truncated {
        reason: IncompleteReason,
    },
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PageRequest {
    pub offset: usize,
    pub limit: usize,
}
impl Default for PageRequest {
    fn default() -> Self {
        Self {
            offset: 0,
            limit: 100,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PageInfo {
    pub offset: usize,
    pub returned: usize,
    pub total: usize,
    pub next_offset: Option<usize>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChildNode {
    pub segment: PathSegment,
    pub label: String,
    pub source: Option<SourceSite>,
    pub node: InspectNode,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InspectNode {
    pub kind: NodeKind,
    pub children: Vec<ChildNode>,
    pub child_count: Option<usize>,
    pub page: Option<PageInfo>,
    pub completeness: Completeness,
}
impl InspectNode {
    pub fn is_complete(&self) -> bool {
        self.completeness == Completeness::Complete
            && match &self.kind {
                NodeKind::Opaque { .. }
                | NodeKind::Redacted
                | NodeKind::Unavailable
                | NodeKind::Truncated { .. }
                | NodeKind::Map {
                    ordering: MapOrdering::Unstable,
                } => false,
                NodeKind::String {
                    preview,
                    total_bytes,
                } => preview.len() == *total_bytes,
                NodeKind::Bytes {
                    preview,
                    total_bytes,
                } => preview.len() == *total_bytes,
                NodeKind::Object { .. }
                | NodeKind::Enum { .. }
                | NodeKind::Sequence { .. }
                | NodeKind::Map { .. } => self.child_count == Some(self.children.len()),
                _ => true,
            }
            && self
                .child_count
                .is_none_or(|count| count == self.children.len())
            && self.page.is_none_or(|page| {
                page.offset == 0
                    && page.returned == self.children.len()
                    && page.total == self.children.len()
                    && page.next_offset.is_none()
            })
            && self.children.iter().all(|c| c.node.is_complete())
    }
    /// Conservative retained payload accounting, including owned node/child headers.
    /// Allocator metadata and application memory are deliberately not claimed here.
    pub fn retained_bytes(&self) -> usize {
        let data = match &self.kind {
            NodeKind::Scalar(Scalar::Integer { decimal, .. })
            | NodeKind::Scalar(Scalar::Float { decimal, .. }) => decimal.len(),
            NodeKind::String { preview, .. } => preview.len(),
            NodeKind::Bytes { preview, .. } => preview.len(),
            NodeKind::Object { name } | NodeKind::Sequence { name } => name.len(),
            NodeKind::Enum {
                name,
                variant_id,
                variant_label,
            } => name
                .len()
                .saturating_add(variant_id.len())
                .saturating_add(variant_label.len()),
            NodeKind::Opaque { label } => label.len(),
            _ => 0,
        };
        self.children.iter().fold(
            std::mem::size_of::<Self>().saturating_add(data),
            |sum, c| {
                sum.saturating_add(std::mem::size_of::<ChildNode>())
                    .saturating_add(c.label.len())
                    .saturating_add(segment_bytes(&c.segment))
                    .saturating_add(c.node.retained_bytes())
            },
        )
    }
}
fn segment_bytes(s: &PathSegment) -> usize {
    match s {
        PathSegment::Field(s)
        | PathSegment::Variant(s)
        | PathSegment::MapKey(MapKey::String(s)) => s.len(),
        PathSegment::MapKey(MapKey::Integer { decimal, .. }) => decimal.len(),
        _ => 0,
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InspectLimits {
    /// Upper bound on built-in node/field/map iterator work per query.
    pub max_work: usize,
    pub max_depth: usize,
    pub max_nodes: usize,
    pub max_bytes: usize,
    pub max_value_bytes: usize,
    pub max_page_size: usize,
}
impl Default for InspectLimits {
    fn default() -> Self {
        Self {
            max_work: 10_000,
            max_depth: 32,
            max_nodes: 1024,
            max_bytes: 256 * 1024,
            max_value_bytes: 4096,
            max_page_size: 100,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InspectQuery {
    pub snapshot: SnapshotId,
    pub path: Vec<PathSegment>,
    pub schema_version: u32,
    pub page: PageRequest,
    pub limits: InspectLimits,
}
impl Default for InspectQuery {
    fn default() -> Self {
        Self {
            snapshot: SnapshotId::default(),
            path: vec![],
            schema_version: DISPLAY_SCHEMA_VERSION,
            page: PageRequest::default(),
            limits: InspectLimits::default(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InspectResult {
    pub snapshot: SnapshotId,
    pub schema: DisplaySchema,
    pub path: Vec<PathSegment>,
    pub node: InspectNode,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum InspectError {
    SchemaMismatch { expected: u32, actual: u32 },
    PathNotFound,
    WrongPathSegment,
    PathTooDeep,
    InvalidPage,
    InvalidMetadata,
    BudgetExceeded,
    WorkLimit,
}
impl fmt::Display for InspectError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "inspection error: {self:?}")
    }
}
impl std::error::Error for InspectError {}

/// Optional ordinary adapter; it introduces no model, codec, Send or Sync bounds.
pub trait Inspect {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError>;
    fn schema(&self) -> DisplaySchema {
        DisplaySchema {
            name: std::any::type_name::<Self>(),
            version: DISPLAY_SCHEMA_VERSION,
            source: None,
        }
    }
}
pub fn inspect(value: &dyn Inspect, query: &InspectQuery) -> Result<InspectResult, InspectError> {
    let schema = value.schema();
    if schema.version != query.schema_version {
        return Err(InspectError::SchemaMismatch {
            expected: schema.version,
            actual: query.schema_version,
        });
    }
    if query.path.len() > query.limits.max_depth {
        return Err(InspectError::PathTooDeep);
    }
    if query.page.limit == 0 || query.page.limit > query.limits.max_page_size {
        return Err(InspectError::InvalidPage);
    }
    // Paths are caller supplied, but copied results must also fit the projection budget.
    let path_bytes = query.path.iter().fold(0usize, |n, p| {
        n.saturating_add(segment_bytes(p))
            .saturating_add(std::mem::size_of::<PathSegment>())
    });
    if path_bytes > query.limits.max_bytes {
        return Err(InspectError::BudgetExceeded);
    }
    let mut cx = InspectContext::new(query.limits, query.page);
    let node = value.inspect(&query.path, &mut cx)?;
    if node.retained_bytes() > query.limits.max_bytes || node_count(&node) > query.limits.max_nodes
    {
        return Err(InspectError::BudgetExceeded);
    }
    Ok(InspectResult {
        snapshot: query.snapshot,
        schema,
        path: query.path.clone(),
        node,
    })
}
fn node_count(node: &InspectNode) -> usize {
    node.children
        .iter()
        .fold(1usize, |n, c| n.saturating_add(node_count(&c.node)))
}

pub struct FieldView<'a> {
    pub id: &'a str,
    pub label: &'a str,
    pub value: Option<&'a dyn Inspect>,
    pub source: Option<SourceSite>,
}
impl<'a> FieldView<'a> {
    pub fn new(id: &'a str, label: &'a str, value: &'a dyn Inspect) -> Self {
        Self {
            id,
            label,
            value: Some(value),
            source: None,
        }
    }
    pub fn redacted(id: &'a str, label: &'a str) -> Self {
        Self {
            id,
            label,
            value: None,
            source: None,
        }
    }
    pub fn with_source(mut self, source: SourceSite) -> Self {
        self.source = Some(source);
        self
    }
}
pub struct InspectContext {
    limits: InspectLimits,
    page: PageRequest,
    depth: usize,
    nodes: usize,
    bytes: usize,
    work: usize,
}
impl InspectContext {
    pub fn new(limits: InspectLimits, page: PageRequest) -> Self {
        Self {
            limits,
            page,
            depth: 0,
            nodes: 0,
            bytes: 0,
            work: 0,
        }
    }
    /// Charge bounded built-in iteration. Application callbacks remain cooperative.
    pub fn charge_work(&mut self, amount: usize) -> Result<(), InspectError> {
        if amount > self.limits.max_work.saturating_sub(self.work) {
            return Err(InspectError::WorkLimit);
        }
        self.work += amount;
        Ok(())
    }
    fn leaf(
        &mut self,
        path: &[PathSegment],
        kind: NodeKind,
        completeness: Completeness,
    ) -> Result<InspectNode, InspectError> {
        if !path.is_empty() {
            return Err(InspectError::PathNotFound);
        }
        self.charge_work(1)?;
        let node = InspectNode {
            kind,
            children: vec![],
            child_count: Some(0),
            page: None,
            completeness,
        };
        self.charge(1, node.retained_bytes())?;
        Ok(node)
    }
    fn charge(&mut self, nodes: usize, bytes: usize) -> Result<(), InspectError> {
        if self.nodes.saturating_add(nodes) > self.limits.max_nodes
            || self.bytes.saturating_add(bytes) > self.limits.max_bytes
        {
            return Err(InspectError::BudgetExceeded);
        }
        self.nodes += nodes;
        self.bytes += bytes;
        Ok(())
    }
    pub fn scalar(
        &mut self,
        path: &[PathSegment],
        value: Scalar,
    ) -> Result<InspectNode, InspectError> {
        self.leaf(path, NodeKind::Scalar(value), Completeness::Complete)
    }
    pub fn string(
        &mut self,
        path: &[PathSegment],
        value: &str,
    ) -> Result<InspectNode, InspectError> {
        let mut end = value.len().min(self.limits.max_value_bytes).min(
            self.limits
                .max_bytes
                .saturating_sub(self.bytes)
                .saturating_sub(std::mem::size_of::<InspectNode>()),
        );
        while !value.is_char_boundary(end) {
            end -= 1;
        }
        self.leaf(
            path,
            NodeKind::String {
                preview: value[..end].to_owned(),
                total_bytes: value.len(),
            },
            if end == value.len() {
                Completeness::Complete
            } else {
                Completeness::Partial(IncompleteReason::ValueLimit)
            },
        )
    }
    pub fn bytes(
        &mut self,
        path: &[PathSegment],
        value: &[u8],
    ) -> Result<InspectNode, InspectError> {
        let end = value.len().min(self.limits.max_value_bytes).min(
            self.limits
                .max_bytes
                .saturating_sub(self.bytes)
                .saturating_sub(std::mem::size_of::<InspectNode>()),
        );
        self.leaf(
            path,
            NodeKind::Bytes {
                preview: value[..end].to_vec(),
                total_bytes: value.len(),
            },
            if end == value.len() {
                Completeness::Complete
            } else {
                Completeness::Partial(IncompleteReason::ValueLimit)
            },
        )
    }
    pub fn redacted(&mut self, path: &[PathSegment]) -> Result<InspectNode, InspectError> {
        if !path.is_empty() {
            return Err(InspectError::PathNotFound);
        }
        self.leaf(
            path,
            NodeKind::Redacted,
            Completeness::Partial(IncompleteReason::Redacted),
        )
    }
    pub fn unavailable(&mut self, path: &[PathSegment]) -> Result<InspectNode, InspectError> {
        self.leaf(
            path,
            NodeKind::Unavailable,
            Completeness::Partial(IncompleteReason::Unavailable),
        )
    }
    pub fn opaque(
        &mut self,
        path: &[PathSegment],
        label: &str,
    ) -> Result<InspectNode, InspectError> {
        let label = bounded_text(label, self.limits.max_value_bytes);
        self.leaf(
            path,
            NodeKind::Opaque { label },
            Completeness::Partial(IncompleteReason::Opaque),
        )
    }
    pub fn truncated(&mut self, reason: IncompleteReason) -> Result<InspectNode, InspectError> {
        self.leaf(
            &[],
            NodeKind::Truncated { reason },
            Completeness::Partial(reason),
        )
    }
    fn metadata(&self, value: &str) -> Result<String, InspectError> {
        if value.len() > 4096 || value.len() > self.limits.max_bytes.saturating_sub(self.bytes) {
            Err(InspectError::InvalidMetadata)
        } else {
            Ok(value.to_owned())
        }
    }
    fn child(&mut self, value: &dyn Inspect) -> Result<InspectNode, InspectError> {
        let old_page = self.page;
        self.page = PageRequest {
            offset: 0,
            limit: old_page.limit.min(self.limits.max_page_size),
        };
        self.depth += 1;
        let result = value.inspect(&[], self);
        self.depth -= 1;
        self.page = old_page;
        result
    }
    fn container<F>(
        &mut self,
        kind: NodeKind,
        total: usize,
        mut child: F,
    ) -> Result<InspectNode, InspectError>
    where
        F: FnMut(usize, &mut Self) -> Result<ChildNode, InspectError>,
    {
        let mut node = self.leaf(&[], kind, Completeness::Complete)?;
        node.child_count = Some(total);
        let offset = self.page.offset.min(total);
        let end = offset
            .saturating_add(self.page.limit.min(self.limits.max_page_size))
            .min(total);
        if self.depth >= self.limits.max_depth && total > 0 {
            node.completeness = Completeness::Partial(IncompleteReason::Depth);
            node.page = Some(PageInfo {
                offset,
                returned: 0,
                total,
                next_offset: Some(offset),
            });
            return Ok(node);
        }
        for index in offset..end {
            let old_nodes = self.nodes;
            let old_bytes = self.bytes;
            match child(index, self) {
                Ok(c) => {
                    let overhead = std::mem::size_of::<ChildNode>()
                        .saturating_add(c.label.len())
                        .saturating_add(segment_bytes(&c.segment));
                    if self.charge(0, overhead).is_err() {
                        self.nodes = old_nodes;
                        self.bytes = old_bytes;
                        node.completeness = Completeness::Partial(IncompleteReason::Bytes);
                        break;
                    }
                    if c.node.completeness != Completeness::Complete
                        && node.completeness == Completeness::Complete
                    {
                        node.completeness = c.node.completeness;
                    }
                    node.children.push(c);
                }
                Err(InspectError::WorkLimit) => {
                    self.nodes = old_nodes;
                    self.bytes = old_bytes;
                    node.completeness = Completeness::Partial(IncompleteReason::Work);
                    break;
                }
                Err(InspectError::BudgetExceeded) => {
                    self.nodes = old_nodes;
                    self.bytes = old_bytes;
                    node.completeness =
                        Completeness::Partial(if old_nodes >= self.limits.max_nodes {
                            IncompleteReason::Nodes
                        } else {
                            IncompleteReason::Bytes
                        });
                    break;
                }
                Err(e) => return Err(e),
            }
        }
        let after = offset + node.children.len();
        if (offset > 0 || after < total) && node.completeness == Completeness::Complete {
            node.completeness = Completeness::Partial(IncompleteReason::Pagination);
        }
        node.page = Some(PageInfo {
            offset,
            returned: node.children.len(),
            total,
            next_offset: if after < total { Some(after) } else { None },
        });
        Ok(node)
    }
    pub fn object(
        &mut self,
        path: &[PathSegment],
        name: &str,
        fields: &[FieldView<'_>],
    ) -> Result<InspectNode, InspectError> {
        if let Some((segment, tail)) = path.split_first() {
            let PathSegment::Field(id) = segment else {
                return Err(InspectError::WrongPathSegment);
            };
            let mut selected = None;
            for field in fields {
                self.charge_work(1)?;
                if field.id == id {
                    selected = Some(field);
                    break;
                }
            }
            let field = selected.ok_or(InspectError::PathNotFound)?;
            return match field.value {
                Some(v) => v.inspect(tail, self),
                None => self.redacted(tail),
            };
        }
        let name = self.metadata(name)?;
        self.container(NodeKind::Object { name }, fields.len(), |i, cx| {
            let f = &fields[i];
            let id = cx.metadata(f.id)?;
            let label = cx.metadata(f.label)?;
            let node = match f.value {
                Some(v) => cx.child(v)?,
                None => cx.redacted(&[])?,
            };
            Ok(ChildNode {
                segment: PathSegment::Field(id),
                label,
                source: f.source,
                node,
            })
        })
    }
    pub fn enumeration(
        &mut self,
        path: &[PathSegment],
        name: &str,
        variant_id: &str,
        variant_label: &str,
        fields: &[FieldView<'_>],
    ) -> Result<InspectNode, InspectError> {
        if let Some((segment, tail)) = path.split_first() {
            if !matches!(segment,PathSegment::Variant(id) if id==variant_id) {
                return Err(InspectError::PathNotFound);
            }
            return self.object(tail, variant_label, fields);
        }
        let name = self.metadata(name)?;
        let variant_id = self.metadata(variant_id)?;
        let variant_label = self.metadata(variant_label)?;
        self.container(
            NodeKind::Enum {
                name,
                variant_id: variant_id.clone(),
                variant_label: variant_label.clone(),
            },
            1,
            |_, cx| {
                cx.depth += 1;
                let old_page = cx.page;
                cx.page = PageRequest {
                    offset: 0,
                    limit: old_page.limit,
                };
                let node = cx.object(&[], &variant_label, fields);
                cx.page = old_page;
                cx.depth -= 1;
                Ok(ChildNode {
                    segment: PathSegment::Variant(variant_id.clone()),
                    label: variant_label.clone(),
                    source: None,
                    node: node?,
                })
            },
        )
    }
    pub fn sequence<F>(
        &mut self,
        path: &[PathSegment],
        name: &str,
        len: usize,
        mut at: F,
    ) -> Result<InspectNode, InspectError>
    where
        F: FnMut(usize, &[PathSegment], &mut Self) -> Result<InspectNode, InspectError>,
    {
        if let Some((segment, tail)) = path.split_first() {
            let PathSegment::Index(i) = segment else {
                return Err(InspectError::WrongPathSegment);
            };
            if *i >= len {
                return Err(InspectError::PathNotFound);
            }
            return at(*i, tail, self);
        }
        let name = self.metadata(name)?;
        self.container(NodeKind::Sequence { name }, len, |i, cx| {
            let old = cx.page;
            cx.page = PageRequest {
                offset: 0,
                limit: old.limit,
            };
            cx.depth += 1;
            let node = at(i, &[], cx);
            cx.depth -= 1;
            cx.page = old;
            Ok(ChildNode {
                segment: PathSegment::Index(i),
                label: i.to_string(),
                source: None,
                node: node?,
            })
        })
    }
    /// Caller supplies stable keys or declares unstable order explicitly. Index
    /// positions are never interpreted as persistent entity identities.
    pub fn map<F>(
        &mut self,
        path: &[PathSegment],
        len: usize,
        ordering: MapOrdering,
        mut entry: F,
    ) -> Result<InspectNode, InspectError>
    where
        F: FnMut(
            Option<&MapKey>,
            usize,
            &[PathSegment],
            &mut Self,
        ) -> Result<(MapKey, InspectNode), InspectError>,
    {
        if let Some((segment, tail)) = path.split_first() {
            let PathSegment::MapKey(key) = segment else {
                return Err(InspectError::WrongPathSegment);
            };
            return entry(Some(key), 0, tail, self).map(|(_, n)| n);
        }
        self.container(NodeKind::Map { ordering }, len, |i, cx| {
            let old = cx.page;
            cx.page = PageRequest {
                offset: 0,
                limit: old.limit,
            };
            cx.depth += 1;
            let result = entry(None, i, &[], cx);
            cx.depth -= 1;
            cx.page = old;
            let (key, node) = result?;
            if segment_bytes(&PathSegment::MapKey(key.clone())) > cx.limits.max_value_bytes {
                return Err(InspectError::InvalidMetadata);
            }
            Ok(ChildNode {
                segment: PathSegment::MapKey(key),
                label: String::new(),
                source: None,
                node,
            })
        })
    }
}
fn bounded_text(value: &str, max: usize) -> String {
    let mut end = value.len().min(max);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_owned()
}

/// Display comparisons cannot establish equality from incomplete projections.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DiffKind {
    Added,
    Removed,
    Changed,
    Unchanged,
    Unknown,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InspectDiff {
    pub before: SnapshotId,
    pub after: SnapshotId,
    pub path: Vec<PathSegment>,
    pub kind: DiffKind,
    pub before_completeness: Option<Completeness>,
    pub after_completeness: Option<Completeness>,
}
pub fn compare_nodes(before: Option<&InspectNode>, after: Option<&InspectNode>) -> DiffKind {
    if before.is_some_and(|n| !n.is_complete()) || after.is_some_and(|n| !n.is_complete()) {
        return DiffKind::Unknown;
    }
    match (before, after) {
        (None, Some(_)) => DiffKind::Added,
        (Some(_), None) => DiffKind::Removed,
        (Some(a), Some(b)) if a == b => DiffKind::Unchanged,
        (Some(_), Some(_)) => DiffKind::Changed,
        _ => DiffKind::Unknown,
    }
}
pub fn diff(before: &InspectResult, after: &InspectResult) -> InspectDiff {
    let compatible = before.schema == after.schema && before.path == after.path;
    InspectDiff {
        before: before.snapshot,
        after: after.snapshot,
        path: after.path.clone(),
        kind: if compatible {
            compare_nodes(Some(&before.node), Some(&after.node))
        } else {
            DiffKind::Unknown
        },
        before_completeness: Some(before.node.completeness),
        after_completeness: Some(after.node.completeness),
    }
}
macro_rules! integer {
    ($($ty:ty => $variant:ident),* $(,)?)=>{$(impl Inspect for $ty{fn inspect(&self,path:&[PathSegment],cx:&mut InspectContext)->Result<InspectNode,InspectError>{cx.scalar(path,Scalar::Integer{kind:IntegerType::$variant,decimal:self.to_string()})}}impl InspectKey for $ty{fn inspect_key(&self)->MapKey{MapKey::Integer{kind:IntegerType::$variant,decimal:self.to_string()}}})*};
}
integer!(i8=>I8,i16=>I16,i32=>I32,i64=>I64,i128=>I128,isize=>Isize,u8=>U8,u16=>U16,u32=>U32,u64=>U64,u128=>U128,usize=>Usize);
impl Inspect for bool {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.scalar(p, Scalar::Bool(*self))
    }
}
impl Inspect for char {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.scalar(p, Scalar::Char(*self))
    }
}
impl Inspect for () {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.scalar(p, Scalar::Unit)
    }
}
impl Inspect for f64 {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.scalar(
            p,
            Scalar::Float {
                kind: "f64",
                decimal: self.to_string(),
                bits: self.to_bits(),
            },
        )
    }
}
impl Inspect for f32 {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.scalar(
            p,
            Scalar::Float {
                kind: "f32",
                decimal: self.to_string(),
                bits: self.to_bits() as u64,
            },
        )
    }
}
impl Inspect for str {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.string(p, self)
    }
}
impl Inspect for String {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.string(p, self)
    }
}
impl<T: Inspect + ?Sized> Inspect for &T {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        (**self).inspect(p, c)
    }
    fn schema(&self) -> DisplaySchema {
        (**self).schema()
    }
}
impl<T: Inspect + ?Sized> Inspect for Box<T> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        (**self).inspect(p, c)
    }
    fn schema(&self) -> DisplaySchema {
        (**self).schema()
    }
}
impl<T: Inspect> Inspect for [T] {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.sequence(p, "slice", self.len(), |i, p, c| self[i].inspect(p, c))
    }
}
impl<T: Inspect> Inspect for Vec<T> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        self.as_slice().inspect(p, c)
    }
}
impl<T: Inspect, const N: usize> Inspect for [T; N] {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        self.as_slice().inspect(p, c)
    }
}
impl<T: Inspect> Inspect for Option<T> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        match self {
            Some(v) => c.enumeration(p, "Option", "Some", "Some", &[FieldView::new("0", "0", v)]),
            None => c.enumeration(p, "Option", "None", "None", &[]),
        }
    }
}
impl<T: Inspect, E: Inspect> Inspect for Result<T, E> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        match self {
            Ok(v) => c.enumeration(p, "Result", "Ok", "Ok", &[FieldView::new("0", "0", v)]),
            Err(v) => c.enumeration(p, "Result", "Err", "Err", &[FieldView::new("0", "0", v)]),
        }
    }
}
pub trait InspectKey {
    fn inspect_key(&self) -> MapKey;
    fn inspect_key_bytes(&self) -> usize {
        segment_bytes(&PathSegment::MapKey(self.inspect_key()))
    }
    fn matches_inspect_key(&self, key: &MapKey) -> bool {
        self.inspect_key() == *key
    }
}
impl InspectKey for String {
    fn inspect_key(&self) -> MapKey {
        MapKey::String(self.clone())
    }
    fn inspect_key_bytes(&self) -> usize {
        self.len()
    }
    fn matches_inspect_key(&self, key: &MapKey) -> bool {
        matches!(key,MapKey::String(s)if self==s)
    }
}
impl InspectKey for &str {
    fn inspect_key(&self) -> MapKey {
        MapKey::String((*self).to_owned())
    }
    fn inspect_key_bytes(&self) -> usize {
        self.len()
    }
    fn matches_inspect_key(&self, key: &MapKey) -> bool {
        matches!(key,MapKey::String(s)if *self==s)
    }
}
impl InspectKey for bool {
    fn inspect_key(&self) -> MapKey {
        MapKey::Bool(*self)
    }
}
impl InspectKey for char {
    fn inspect_key(&self) -> MapKey {
        MapKey::Char(*self)
    }
}
impl<K: InspectKey + Ord, V: Inspect> Inspect for BTreeMap<K, V> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.map(p, self.len(), MapOrdering::Stable, |key, index, p, c| {
            let (k, v) = if let Some(key) = key {
                let mut selected = None;
                for (k, v) in self {
                    c.charge_work(1)?;
                    if k.matches_inspect_key(key) {
                        selected = Some((k, v));
                        break;
                    }
                }
                selected
            } else {
                c.charge_work(index.saturating_add(1))?;
                self.iter().nth(index)
            }
            .ok_or(InspectError::PathNotFound)?;
            let key_bytes = k.inspect_key_bytes();
            if key_bytes > c.limits.max_value_bytes
                || key_bytes > c.limits.max_bytes.saturating_sub(c.bytes)
            {
                return Err(InspectError::BudgetExceeded);
            }
            Ok((k.inspect_key(), v.inspect(p, c)?))
        })
    }
}

/// Explicit bytes adapter, distinct from a sequence of numeric u8 values.
pub struct Bytes<'a>(pub &'a [u8]);
impl Inspect for Bytes<'_> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.bytes(p, self.0)
    }
}
/// Opaque labels must be safe metadata; this never formats an underlying value.
pub struct Opaque<'a>(pub &'a str);
impl Inspect for Opaque<'_> {
    fn inspect(
        &self,
        p: &[PathSegment],
        c: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        c.opaque(p, self.0)
    }
}

// Handwritten adapters for the linked, finite request-lifecycle harness.
impl Inspect for stateless::demo::State {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            path,
            "request-lifecycle.State",
            &[
                FieldView::new("generation", "generation", &self.generation),
                FieldView::new("active", "active", &self.active),
                FieldView::new("ready", "ready", &self.ready),
                FieldView::new("pending", "pending", &self.pending),
            ],
        )
    }
    fn schema(&self) -> DisplaySchema {
        DisplaySchema {
            name: "request-lifecycle.State",
            version: 1,
            source: None,
        }
    }
}
impl Inspect for stateless::demo::Input {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        match self {
            Self::Start => cx.enumeration(path, "request-lifecycle.Input", "Start", "Start", &[]),
            Self::Cancel => {
                cx.enumeration(path, "request-lifecycle.Input", "Cancel", "Cancel", &[])
            }
            Self::Complete(generation) => cx.enumeration(
                path,
                "request-lifecycle.Input",
                "Complete",
                "Complete",
                &[FieldView::new("generation", "generation", generation)],
            ),
        }
    }
}
impl Inspect for stateless::demo::Output {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        let (variant, generation) = match self {
            Self::Request(generation) => ("Request", generation),
            Self::Publish(generation) => ("Publish", generation),
            Self::Release(generation) => ("Release", generation),
        };
        cx.enumeration(
            path,
            "request-lifecycle.Output",
            variant,
            variant,
            &[FieldView::new("generation", "generation", generation)],
        )
    }
}

impl<T: Inspect> Inspect for std::collections::VecDeque<T> {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.sequence(path, "VecDeque", self.len(), |i, path, cx| {
            self[i].inspect(path, cx)
        })
    }
}
macro_rules! tuples {
    ($(($($type:ident:$index:tt),+)),+ $(,)?)=>{$(impl<$($type:Inspect),+> Inspect for ($($type,)+){fn inspect(&self,path:&[PathSegment],cx:&mut InspectContext)->Result<InspectNode,InspectError>{cx.object(path,"tuple",&[$(FieldView::new(stringify!($index),stringify!($index),&self.$index)),+])}})+};
}
tuples!((A:0),(A:0,B:1),(A:0,B:1,C:2),(A:0,B:1,C:2,D:3),(A:0,B:1,C:2,D:3,E:4),(A:0,B:1,C:2,D:3,E:4,F:5),(A:0,B:1,C:2,D:3,E:4,F:5,G:6),(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7));

/// Explicit convenience only: Debug formatting is unstable and may disclose
/// secrets. It is never parsed as a schema or accepted as display equality.
/// Formatting stops at the preview limit; user Debug code remains cooperative.
pub struct DebugView<'a, T: fmt::Debug + ?Sized>(pub &'a T);
impl<T: fmt::Debug + ?Sized> Inspect for DebugView<'_, T> {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        if !path.is_empty() {
            return Err(InspectError::PathNotFound);
        }
        struct Writer {
            value: String,
            limit: usize,
        }
        impl fmt::Write for Writer {
            fn write_str(&mut self, value: &str) -> fmt::Result {
                let remaining = self.limit.saturating_sub(self.value.len());
                let mut end = value.len().min(remaining);
                while !value.is_char_boundary(end) {
                    end -= 1;
                }
                self.value.push_str(&value[..end]);
                if end < value.len() {
                    Err(fmt::Error)
                } else {
                    Ok(())
                }
            }
        }
        let mut writer = Writer {
            value: String::new(),
            limit: cx.limits.max_value_bytes.min(
                cx.limits
                    .max_bytes
                    .saturating_sub(cx.bytes)
                    .saturating_sub(std::mem::size_of::<InspectNode>()),
            ),
        };
        let _ = fmt::write(&mut writer, format_args!("{:?}", self.0));
        cx.opaque(path, &writer.value)
    }
}
