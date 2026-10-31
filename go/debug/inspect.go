package debug

import (
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"unicode/utf8"
	"unsafe"
)

const DisplaySchemaVersion uint32 = 1

type SnapshotID struct{ Session, Revision, Sequence uint64 }
type SourceSite struct {
	File         string
	Line, Column uint32
}
type DisplaySchema struct {
	Name    string
	Version uint32
	Source  *SourceSite
}

// Segment preserves kind and typed key identity. Display labels are not keys.
type Segment struct {
	Kind, Name, KeyType, Decimal string
	Index                        uint64
}

func FieldPath(name string) Segment  { return Segment{Kind: "field", Name: name} }
func IndexPath(index uint64) Segment { return Segment{Kind: "index", Index: index} }

type Scalar struct {
	Kind, Decimal, Text string
	Bits                uint64
	Bool                bool
}

// FloatScalar represents Rust's Float kind and its independent text and bits.
// Existing f32 and f64 values retain their established representations.
func FloatScalar(kind, decimal string, bits uint64) Scalar {
	if kind == "f32" || kind == "f64" {
		return Scalar{Kind: kind, Decimal: decimal, Bits: bits}
	}
	return Scalar{Kind: "float", Text: kind, Decimal: decimal, Bits: bits}
}

type InspectNode struct {
	Kind, Name, VariantID, VariantLabel, Text, Ordering, Incomplete string
	Scalar                                                          *Scalar
	Bytes                                                           []byte
	TotalBytes                                                      uint64
	Children                                                        []ChildNode
	ChildCount                                                      *uint64
	Page                                                            *PageInfo
	engineExhausted                                                 bool
}
type ChildNode struct {
	Segment Segment
	Label   string
	Source  *SourceSite
	Node    InspectNode
}
type PageInfo struct {
	Offset, Returned, Total uint64
	NextOffset              *uint64
}

const segmentHeader = uint64(unsafe.Sizeof(Segment{}))
const inspectResultHeader = uint64(unsafe.Sizeof(InspectResult{})) - uint64(unsafe.Sizeof(InspectNode{}))
const inspectNodeHeader = uint64(unsafe.Sizeof(InspectNode{}))
const inspectChildHeader = uint64(unsafe.Sizeof(ChildNode{})) - inspectNodeHeader

// canonicalInspectSegment preserves the active tagged-union identity.
func canonicalInspectSegment(segment Segment) (Segment, bool) {
	switch segment.Kind {
	case "field", "variant":
		if !utf8.ValidString(segment.Name) {
			return Segment{}, false
		}
		segment = Segment{Kind: segment.Kind, Name: segment.Name}
	case "index":
		segment = Segment{Kind: segment.Kind, Index: segment.Index}
	case "map_key":
		switch segment.KeyType {
		case "string":
			if !utf8.ValidString(segment.Name) {
				return Segment{}, false
			}
		case "char":
			if !utf8.ValidString(segment.Name) || utf8.RuneCountInString(segment.Name) != 1 {
				return Segment{}, false
			}
		case "bool":
			if segment.Decimal != "true" && segment.Decimal != "false" {
				return Segment{}, false
			}
		case "i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize":
			// Rust stores decimal as UTF-8 text; preserve it without numeric coercion.
			if !utf8.ValidString(segment.Decimal) {
				return Segment{}, false
			}

		default:
			return Segment{}, false
		}
		if segment.KeyType == "string" || segment.KeyType == "char" {
			segment = Segment{Kind: segment.Kind, KeyType: segment.KeyType, Name: segment.Name}
		} else {
			segment = Segment{Kind: segment.Kind, KeyType: segment.KeyType, Decimal: segment.Decimal}
		}
	default:
		return Segment{}, false
	}
	return segment, true
}

func inspectChildSegmentKind(kind string) string {
	switch kind {
	case "object":
		return "field"
	case "sequence":
		return "index"
	case "map":
		return "map_key"
	case "enum":
		return "variant"
	default:
		return ""
	}
}

func (n InspectNode) IsComplete() bool {
	if n.Incomplete != "" {
		return false
	}
	for _, value := range []string{n.Name, n.VariantID, n.VariantLabel, n.Text} {
		if !utf8.ValidString(value) {
			return false
		}
	}
	switch n.Kind {
	case "scalar":
		if n.Scalar == nil || validateScalar(*n.Scalar) != nil {
			return false
		}
	case "object", "enum", "sequence":
		if n.ChildCount == nil {
			return false
		}
	case "redacted", "opaque", "unavailable", "truncated":
		return false
	case "map":
		if n.Ordering != "stable" || n.ChildCount == nil {
			return false
		}
	case "string":
		if uint64(len(n.Text)) != n.TotalBytes {
			return false
		}
	case "bytes":
		if uint64(len(n.Bytes)) != n.TotalBytes {
			return false
		}
	default:
		return false
	}
	if n.ChildCount != nil && *n.ChildCount != uint64(len(n.Children)) {
		return false
	}
	if n.Page != nil && (n.Page.Offset != 0 || n.Page.Returned != uint64(len(n.Children)) || n.Page.Total != uint64(len(n.Children)) || n.Page.NextOffset != nil) {
		return false
	}
	segments := make(map[Segment]struct{}, len(n.Children))
	for i, c := range n.Children {
		if !utf8.ValidString(c.Label) || (c.Source != nil && !utf8.ValidString(c.Source.File)) {
			return false
		}
		segment, ok := canonicalInspectSegment(c.Segment)
		if !ok || segment.Kind != inspectChildSegmentKind(n.Kind) || (n.Kind == "sequence" && segment.Index != uint64(i)) {
			return false
		}
		if _, exists := segments[segment]; exists {
			return false
		}
		segments[segment] = struct{}{}
		if !c.Node.IsComplete() {
			return false
		}
	}
	return true
}

type InspectLimits struct{ MaxWork, MaxDepth, MaxNodes, MaxBytes, MaxValueBytes, MaxPageSize uint64 }

func DefaultInspectLimits() InspectLimits {
	return InspectLimits{10000, 32, 1024, 256 * 1024, 4096, 100}
}

type InspectQuery struct {
	Snapshot      SnapshotID
	Path          []Segment
	SchemaVersion uint32
	Offset, Limit uint64
	Limits        InspectLimits
}

func DefaultInspectQuery() InspectQuery {
	return InspectQuery{SchemaVersion: 1, Limit: 100, Limits: DefaultInspectLimits()}
}

type InspectResult struct {
	Snapshot SnapshotID
	Schema   DisplaySchema
	Path     []Segment
	Node     InspectNode
}

// InspectAdapter is explicit and display-only. It has no codec/equality meaning.
type InspectAdapter interface {
	DisplaySchema() DisplaySchema
	Inspect([]Segment, *InspectContext) (InspectNode, error)
}
type InspectContext struct {
	limits                                   InspectLimits
	offset, limit, work, nodes, bytes, depth uint64
}

func Inspect(value InspectAdapter, q InspectQuery) (InspectResult, error) {
	return inspect(value, q, false)
}

// Protocol inspection sends only the schema name and version. Rust does not
// charge this borrowed metadata against the node projection budget.
func inspect(value InspectAdapter, q InspectQuery, protocol bool) (InspectResult, error) {
	r := InspectResult{Snapshot: q.Snapshot}
	if value == nil {
		return r, fmt.Errorf("nil inspect adapter")
	}
	schema, err := inspectSchemaSafely(value)
	if err != nil {
		return r, err
	}
	r.Schema = schema
	if r.Schema.Version != q.SchemaVersion {
		return r, fmt.Errorf("schema mismatch")
	}
	if uint64(len(q.Path)) > q.Limits.MaxDepth {
		return r, fmt.Errorf("path too deep")
	}
	if q.Limit == 0 || q.Limit > q.Limits.MaxPageSize {
		return r, fmt.Errorf("invalid page")
	}
	cx := &InspectContext{limits: q.Limits, offset: q.Offset, limit: q.Limit}
	if !protocol {
		if !cx.charge(inspectResultHeader + uint64(len(r.Schema.Name))) {
			return r, fmt.Errorf("schema metadata byte limit")
		}
		r.Schema.Name = strings.Clone(r.Schema.Name)
	}
	if !protocol && r.Schema.Source != nil {
		source, e := cx.source(r.Schema.Source)
		if e != nil {
			return r, e
		}
		r.Schema.Source = source
	} else if protocol {
		r.Schema.Source = nil
	}
	for _, p := range q.Path {
		if !cx.charge(uint64(len(p.Kind)+len(p.Name)+len(p.KeyType)+len(p.Decimal)) + segmentHeader) {
			return r, fmt.Errorf("path exceeds byte budget")
		}
	}
	r.Path = append([]Segment{}, q.Path...)
	for i, p := range r.Path {
		if uint64(len(p.Kind)+len(p.Name)+len(p.KeyType)+len(p.Decimal)) > q.Limits.MaxValueBytes {
			return r, fmt.Errorf("path value limit")
		}
		if _, valid := canonicalInspectSegment(p); !valid {
			return r, fmt.Errorf("invalid path segment")
		}
		r.Path[i] = cloneSegment(p)
	}
	metadataBytes := cx.bytes
	// Projection callbacks own their argument and may mutate it. Keep both the
	// caller's query and the retained result path isolated from those mutations.
	callbackPath := append([]Segment(nil), r.Path...)
	node, e := inspectValueSafely(value, callbackPath, cx)
	if e != nil {
		return r, e
	}
	admission := q.Limits
	admission.MaxBytes -= metadataBytes
	copied, err := cloneProjection(node, admission)
	if err != nil {
		return r, err
	}
	r.Node = copied
	return r, nil
}

func inspectSchemaSafely(value InspectAdapter) (schema DisplaySchema, err error) {
	defer func() {
		if recover() != nil {
			err = fmt.Errorf("inspection callback panicked")
		}
	}()
	return value.DisplaySchema(), nil
}

func inspectValueSafely(value InspectAdapter, path []Segment, cx *InspectContext) (node InspectNode, err error) {
	defer func() {
		if recover() != nil {
			err = fmt.Errorf("inspection callback panicked")
		}
	}()
	return value.Inspect(path, cx)
}
func (c *InspectContext) charge(n uint64) bool {
	if n > c.limits.MaxBytes-c.bytes {
		return false
	}
	c.bytes += n
	return true
}
func (c *InspectContext) spend() bool {
	if c.work >= c.limits.MaxWork {
		return false
	}
	c.work++
	return true
}
func exhaustedChild(n InspectNode) bool {
	return n.engineExhausted
}
func engineTruncated(reason string) InspectNode {
	return InspectNode{Kind: "truncated", Incomplete: reason, engineExhausted: reason == "work" || reason == "nodes" || reason == "bytes"}
}
func (c *InspectContext) node(kind string) (InspectNode, bool) {
	if !c.spend() {
		return engineTruncated("work"), false
	}
	if c.depth > c.limits.MaxDepth {
		return InspectNode{Kind: "truncated", Incomplete: "depth"}, false
	}
	if c.nodes >= c.limits.MaxNodes {
		return engineTruncated("nodes"), false
	}
	if !c.charge(inspectNodeHeader) {
		return engineTruncated("bytes"), false
	}
	c.nodes++
	// Successful leaf projections have a known empty child set. Containers
	// replace this with their actual count when their metadata is admitted.
	count := uint64(0)
	return InspectNode{Kind: kind, ChildCount: &count}, true
}
func (c *InspectContext) Scalar(path []Segment, value Scalar) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	if uint64(len(value.Kind)+len(value.Decimal)+len(value.Text)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if e := validateScalar(value); e != nil {
		return InspectNode{}, e
	}
	n, ok := c.node("scalar")
	if !ok {
		return n, nil
	}
	if uint64(len(value.Kind)+len(value.Decimal)+len(value.Text)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(64 + uint64(len(value.Kind)+len(value.Decimal)+len(value.Text))) {
		return engineTruncated("bytes"), nil
	}
	v := value
	v.Kind = strings.Clone(v.Kind)
	v.Decimal = strings.Clone(v.Decimal)
	v.Text = strings.Clone(v.Text)
	n.Scalar = &v
	return n, nil
}
func (c *InspectContext) String(path []Segment, value string) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	n, ok := c.node("string")
	if !ok {
		return n, nil
	}
	n.TotalBytes = uint64(len(value))
	maximum := min(uint64(len(value)), c.limits.MaxValueBytes, c.limits.MaxBytes-c.bytes)
	end := 0
	// Go strings can contain invalid UTF-8 after the retained preview. Validate
	// the whole string with bounded work before publishing it as a string.
	for i := 0; i < len(value); {
		if !c.spend() {
			return engineTruncated("work"), nil
		}
		r, size := utf8.DecodeRuneInString(value[i:])
		if r == utf8.RuneError && size == 1 {
			return InspectNode{}, fmt.Errorf("invalid string UTF-8")
		}
		i += size
		if uint64(i) <= maximum {
			end = i
		}
	}
	n.Text = strings.Clone(value[:end])
	if !c.charge(uint64(end)) {
		return InspectNode{}, fmt.Errorf("budget exceeded")
	}
	if uint64(end) < n.TotalBytes {
		n.Incomplete = "value_limit"
	}
	return n, nil
}
func (c *InspectContext) Bytes(path []Segment, value []byte) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	n, ok := c.node("bytes")
	if !ok {
		return n, nil
	}
	n.TotalBytes = uint64(len(value))
	maximum := min(n.TotalBytes, c.limits.MaxValueBytes, c.limits.MaxBytes-c.bytes)
	n.Bytes = append([]byte{}, value[:maximum]...)
	c.charge(maximum)
	if maximum < n.TotalBytes {
		n.Incomplete = "value_limit"
	}
	return n, nil
}

type InspectField struct {
	Segment  Segment
	Label    string
	Source   *SourceSite
	Redacted bool
	Value    func([]Segment, *InspectContext) (InspectNode, error)
}

func (c *InspectContext) Object(path []Segment, name string, fields []InspectField) (InspectNode, error) {
	return c.container(path, "object", name, "", fields, false)
}
func (c *InspectContext) Sequence(path []Segment, name string, fields []InspectField) (InspectNode, error) {
	return c.container(path, "sequence", name, "", fields, false)
}

// SequenceIndexed constructs only selected page entries. The caller owns the
// backing data and must keep it stable throughout the inspection callback.
func (c *InspectContext) SequenceIndexed(path []Segment, name string, total uint64, at func(uint64, []Segment, *InspectContext) (InspectNode, error)) (InspectNode, error) {
	if at == nil {
		return InspectNode{}, fmt.Errorf("sequence accessor required")
	}
	if len(path) > 0 {
		segment, ok := canonicalInspectSegment(path[0])
		if !ok || segment.Kind != "index" {
			return InspectNode{}, fmt.Errorf("invalid sequence path")
		}
		if segment.Index >= total {
			return InspectNode{}, fmt.Errorf("path not found")
		}
		return at(segment.Index, path[1:], c)
	}
	n, ok := c.node("sequence")
	if !ok {
		return n, nil
	}
	if uint64(len(name)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(uint64(len(name))) || !c.charge(40) {
		return engineTruncated("bytes"), nil
	}
	n.Name = strings.Clone(name)
	n.ChildCount = &total
	page := PageInfo{Offset: min(c.offset, total), Total: total}
	if c.depth >= c.limits.MaxDepth && total > 0 {
		n.Incomplete = "depth"
		next := page.Offset
		page.NextOffset = &next
		n.Page = &page
		return n, nil
	}
	end := page.Offset + min(c.limit, total-page.Offset)
	for index := page.Offset; index < end; index++ {
		oldNodes, oldBytes := c.nodes, c.bytes
		if !c.spend() {
			n.Incomplete = "work"
			break
		}
		label := strconv.FormatUint(index, 10)
		segment := IndexPath(index)
		if uint64(len(label)+len(segment.Kind)) > c.limits.MaxValueBytes {
			n.Incomplete = "value_limit"
			break
		}
		child, err := func() (InspectNode, error) {
			oldOffset := c.offset
			c.offset = 0
			c.depth++
			defer func() { c.depth--; c.offset = oldOffset }()
			return at(index, nil, c)
		}()
		if err != nil {
			return InspectNode{}, err
		}
		if exhaustedChild(child) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = child.Incomplete
			break
		}
		if !c.charge(inspectChildHeader + uint64(len(label)+len(segment.Kind))) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = "bytes"
			break
		}
		n.Children = append(n.Children, ChildNode{Segment: segment, Label: label, Node: child})
		if child.Incomplete != "" && n.Incomplete == "" {
			n.Incomplete = child.Incomplete
		}
	}
	page.Returned = uint64(len(n.Children))
	if page.Offset+page.Returned < total {
		next := page.Offset + page.Returned
		page.NextOffset = &next
	}
	n.Page = &page
	if (page.Offset > 0 || page.Returned < total) && n.Incomplete == "" {
		n.Incomplete = "pagination"
	}
	return n, nil
}
func (c *InspectContext) Map(path []Segment, ordering string, fields []InspectField) (InspectNode, error) {
	if ordering != "stable" && ordering != "unstable" {
		return InspectNode{}, fmt.Errorf("map ordering declaration required")
	}
	return c.container(path, "map", "", ordering, fields, false)
}

// MapCanonical emits Rust-compatible map children: an empty label and no source.
// Supplied label and source are ignored; keys, ordering and values are preserved.
func (c *InspectContext) MapCanonical(path []Segment, ordering string, fields []InspectField) (InspectNode, error) {
	if ordering != "stable" && ordering != "unstable" {
		return InspectNode{}, fmt.Errorf("map ordering declaration required")
	}
	return c.container(path, "map", "", ordering, fields, true)
}

// MapIndexed obtains only page metadata for page queries. Selected-key queries
// scan metadata within MaxWork to prove the requested identity is unique; no
// value callback is invoked until that scan completes. The backing map must
// remain stable while the callback runs and the declared order must be honest.
func (c *InspectContext) MapIndexed(path []Segment, ordering string, total uint64, entry func(uint64) (InspectField, error)) (InspectNode, error) {
	if ordering != "stable" && ordering != "unstable" {
		return InspectNode{}, fmt.Errorf("map ordering declaration required")
	}
	return c.indexedFields(path, "map", "", ordering, "map_key", total, entry, false)
}

// MapIndexedCanonical is the lazy equivalent of MapCanonical.
func (c *InspectContext) MapIndexedCanonical(path []Segment, ordering string, total uint64, entry func(uint64) (InspectField, error)) (InspectNode, error) {
	if ordering != "stable" && ordering != "unstable" {
		return InspectNode{}, fmt.Errorf("map ordering declaration required")
	}
	return c.indexedFields(path, "map", "", ordering, "map_key", total, entry, true)
}

// ObjectIndexed supports large dynamic objects without first materializing all
// field descriptors. Selected fields are scanned under the work budget to
// establish uniqueness before a value callback is invoked.
func (c *InspectContext) ObjectIndexed(path []Segment, name string, total uint64, field func(uint64) (InspectField, error)) (InspectNode, error) {
	return c.indexedFields(path, "object", name, "", "field", total, field, false)
}

func (c *InspectContext) indexedFields(path []Segment, kind, name, ordering, segmentKind string, total uint64, entry func(uint64) (InspectField, error), canonicalMap bool) (InspectNode, error) {
	if entry == nil {
		return InspectNode{}, fmt.Errorf("indexed entry accessor required")
	}
	if len(path) > 0 {
		if reason := c.mapMetadataBound(path[0], "", nil); reason != "" {
			return engineTruncated(reason), nil
		}
		target, ok := canonicalInspectSegment(path[0])
		if !ok || target.Kind != segmentKind {
			return InspectNode{}, fmt.Errorf("invalid indexed path")
		}
		var selected InspectField
		found := false
		for i := uint64(0); i < total; i++ {
			if !c.spend() {
				return engineTruncated("work"), nil
			}
			field, err := entry(i)
			if err != nil {
				return InspectNode{}, err
			}
			if canonicalMap {
				field.Label, field.Source = "", nil
			}
			if reason := c.mapMetadataBound(field.Segment, field.Label, field.Source); reason != "" {
				return engineTruncated(reason), nil
			}
			if !validIndexedMetadata(field) {
				return InspectNode{}, fmt.Errorf("invalid indexed entry metadata")
			}
			segment, valid := canonicalInspectSegment(field.Segment)
			if !valid || segment.Kind != segmentKind {
				return InspectNode{}, fmt.Errorf("invalid indexed entry metadata")
			}
			if segment == target {
				if found {
					return InspectNode{}, fmt.Errorf("ambiguous path segment")
				}
				selected = field
				found = true
			}
		}
		if !found {
			return InspectNode{}, fmt.Errorf("path not found")
		}
		return c.selectedField(selected, path[1:])
	}
	n, ok := c.node(kind)
	if !ok {
		return n, nil
	}
	if uint64(len(name)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(uint64(len(name))) || !c.charge(40) {
		return engineTruncated("bytes"), nil
	}
	n.Ordering = ordering
	n.Name = strings.Clone(name)
	n.ChildCount = &total
	page := PageInfo{Offset: min(c.offset, total), Total: total}
	if c.depth >= c.limits.MaxDepth && total > 0 {
		n.Incomplete = "depth"
		next := page.Offset
		page.NextOffset = &next
		n.Page = &page
		return n, nil
	}
	end := page.Offset + min(c.limit, total-page.Offset)
	for i := page.Offset; i < end; i++ {
		oldNodes, oldBytes := c.nodes, c.bytes
		if !c.spend() {
			n.Incomplete = "work"
			break
		}
		field, err := entry(i)
		if err != nil {
			return InspectNode{}, err
		}
		if canonicalMap {
			field.Label, field.Source = "", nil
		}
		if reason := c.mapMetadataBound(field.Segment, field.Label, field.Source); reason != "" {
			n.Incomplete = reason
			break
		}
		if !validIndexedMetadata(field) {
			return InspectNode{}, fmt.Errorf("invalid indexed entry metadata")
		}
		segment, valid := canonicalInspectSegment(field.Segment)
		if !valid || segment.Kind != segmentKind {
			return InspectNode{}, fmt.Errorf("invalid indexed entry metadata")
		}
		size := uint64(len(field.Label) + len(segment.Kind) + len(segment.Name) + len(segment.KeyType) + len(segment.Decimal))
		if size > c.limits.MaxValueBytes {
			n.Incomplete = "value_limit"
			break
		}
		sourceSnapshot := cloneInspectSource(field.Source)
		child, err := func() (InspectNode, error) {
			old := c.offset
			c.offset = 0
			defer func() { c.offset = old }()
			return c.field(field, nil)
		}()
		if err != nil {
			return InspectNode{}, err
		}
		if exhaustedChild(child) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = child.Incomplete
			break
		}
		if !c.charge(inspectChildHeader + size) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = "bytes"
			break
		}
		source, err := c.source(sourceSnapshot)
		if err != nil {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = "bytes"
			break
		}
		n.Children = append(n.Children, ChildNode{Segment: cloneSegment(segment), Label: strings.Clone(field.Label), Source: source, Node: child})
		if child.Incomplete != "" && n.Incomplete == "" {
			n.Incomplete = child.Incomplete
		}
	}
	page.Returned = uint64(len(n.Children))
	if page.Offset+page.Returned < total {
		next := page.Offset + page.Returned
		page.NextOffset = &next
	}
	n.Page = &page
	if (page.Offset > 0 || page.Returned < total) && n.Incomplete == "" {
		n.Incomplete = "pagination"
	}
	return n, nil
}

func validIndexedMetadata(field InspectField) bool {
	return utf8.ValidString(field.Label) && (field.Source == nil || utf8.ValidString(field.Source.File))
}

// Bound returned metadata before UTF-8 validation, integer parsing or equality.
func (c *InspectContext) mapMetadataBound(segment Segment, label string, source *SourceSite) string {
	remainingValue := c.limits.MaxValueBytes
	remainingBytes := c.limits.MaxBytes - c.bytes
	values := []string{segment.Kind, segment.Name, segment.KeyType, segment.Decimal, label}
	if source != nil {
		values = append(values, source.File)
	}
	for _, value := range values {
		size := uint64(len(value))
		if size > remainingValue {
			return "value_limit"
		}
		remainingValue -= size
		if size > remainingBytes {
			return "bytes"
		}
		remainingBytes -= size
	}
	return ""
}
func (c *InspectContext) field(f InspectField, path []Segment) (InspectNode, error) {
	if f.Redacted {
		n, ok := c.node("redacted")
		if !ok {
			return n, nil
		}
		n.Incomplete = "redacted"
		return n, nil
	}
	if f.Value == nil {
		n, ok := c.node("unavailable")
		if !ok {
			return n, nil
		}
		n.Incomplete = "unavailable"
		return n, nil
	}
	c.depth++
	oldOffset := c.offset
	c.offset = 0
	defer func() {
		c.offset = oldOffset
		c.depth--
	}()
	return f.Value(path, c)
}
func (c *InspectContext) selectedField(f InspectField, path []Segment) (InspectNode, error) {
	if f.Redacted || f.Value == nil {
		if len(path) > 0 {
			return InspectNode{}, fmt.Errorf("path not found")
		}
		return c.field(f, nil)
	}
	return f.Value(path, c)
}
func (c *InspectContext) container(path []Segment, kind, name, ordering string, fields []InspectField, canonicalMap bool) (InspectNode, error) {
	expectedKind := inspectChildSegmentKind(kind)
	if expectedKind == "" {
		return InspectNode{}, fmt.Errorf("unknown container kind")
	}
	if len(path) > 0 {
		if reason := c.mapMetadataBound(path[0], "", nil); reason != "" {
			return engineTruncated(reason), nil
		}
		target, ok := canonicalInspectSegment(path[0])
		if !ok || target.Kind != expectedKind {
			return InspectNode{}, fmt.Errorf("invalid path segment")
		}
		if kind == "sequence" {
			if target.Index >= uint64(len(fields)) {
				return InspectNode{}, fmt.Errorf("path not found")
			}
			field := fields[target.Index]
			field.Label, field.Source = "", nil
			if reason := c.mapMetadataBound(field.Segment, field.Label, field.Source); reason != "" {
				return engineTruncated(reason), nil
			}
			segment, valid := canonicalInspectSegment(field.Segment)
			if !valid || segment.Kind != "index" || segment.Index != target.Index || !validIndexedMetadata(field) {
				return InspectNode{}, fmt.Errorf("invalid sequence entry metadata")
			}
			return c.selectedField(field, path[1:])
		}
		selected := -1
		for i, f := range fields {
			if !c.spend() {
				return engineTruncated("work"), nil
			}
			if reason := c.mapMetadataBound(f.Segment, "", nil); reason != "" {
				return engineTruncated(reason), nil
			}
			candidate, valid := canonicalInspectSegment(f.Segment)
			if !valid || candidate.Kind != expectedKind || (kind == "sequence" && candidate.Index != uint64(i)) {
				return InspectNode{}, fmt.Errorf("invalid container segment")
			}
			if candidate == target {
				if selected >= 0 {
					return InspectNode{}, fmt.Errorf("ambiguous path segment")
				}
				selected = i
			}
		}
		if selected < 0 {
			return InspectNode{}, fmt.Errorf("path not found")
		}
		field := fields[selected]
		if canonicalMap {
			field.Label, field.Source = "", nil
		}
		if reason := c.mapMetadataBound(field.Segment, field.Label, field.Source); reason != "" {
			return engineTruncated(reason), nil
		}
		if !validIndexedMetadata(field) {
			return InspectNode{}, fmt.Errorf("invalid field metadata")
		}
		return c.selectedField(field, path[1:])
	}
	n, ok := c.node(kind)
	if !ok {
		return n, nil
	}
	if uint64(len(name)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(uint64(len(name))) {
		return engineTruncated("bytes"), nil
	}
	n.Name = strings.Clone(name)
	n.Ordering = ordering
	if !c.charge(40) {
		return engineTruncated("bytes"), nil
	}
	count := uint64(len(fields))
	n.ChildCount = &count
	page := PageInfo{Offset: min(c.offset, count), Total: count}
	if c.depth >= c.limits.MaxDepth && count > 0 {
		n.Incomplete = "depth"
		next := page.Offset
		page.NextOffset = &next
		n.Page = &page
		return n, nil
	}
	end := page.Offset + min(c.limit, count-page.Offset)
	for i := page.Offset; i < end; i++ {
		if !c.spend() {
			n.Incomplete = "work"
			break
		}
		oldNodes, oldBytes := c.nodes, c.bytes
		f := fields[i]
		if canonicalMap {
			f.Label, f.Source = "", nil
		}
		if kind == "sequence" {
			f.Label, f.Source = strconv.FormatUint(i, 10), nil
		}
		if reason := c.mapMetadataBound(f.Segment, f.Label, f.Source); reason != "" {
			n.Incomplete = reason
			break
		}
		if !validIndexedMetadata(f) {
			return InspectNode{}, fmt.Errorf("invalid field metadata")
		}
		if segment, valid := canonicalInspectSegment(f.Segment); !valid || segment.Kind != expectedKind || (kind == "sequence" && segment.Index != i) {
			return InspectNode{}, fmt.Errorf("invalid field segment")
		}
		if uint64(len(f.Label)+len(f.Segment.Name)+len(f.Segment.Kind)+len(f.Segment.KeyType)+len(f.Segment.Decimal)) > c.limits.MaxValueBytes {
			n.Incomplete = "value_limit"
			break
		}
		sourceSnapshot := cloneInspectSource(f.Source)
		child, e := c.field(f, nil)
		if e != nil {
			return InspectNode{}, e
		}
		if exhaustedChild(child) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = child.Incomplete
			break
		}
		if !c.charge(inspectChildHeader + uint64(len(f.Label)+len(f.Segment.Kind)+len(f.Segment.Name)+len(f.Segment.KeyType)+len(f.Segment.Decimal))) {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = "bytes"
			break
		}
		source, e := c.source(sourceSnapshot)
		if e != nil {
			c.nodes, c.bytes = oldNodes, oldBytes
			n.Incomplete = "bytes"
			break
		}
		n.Children = append(n.Children, ChildNode{cloneSegment(f.Segment), strings.Clone(f.Label), source, child})
		// A parent cannot claim complete evidence when an admitted child is
		// partial, even if all requested children fit on the current page.
		if child.Incomplete != "" && n.Incomplete == "" {
			n.Incomplete = child.Incomplete
		}
	}
	page.Returned = uint64(len(n.Children))
	if page.Offset+page.Returned < count {
		x := page.Offset + page.Returned
		page.NextOffset = &x
	}
	n.Page = &page
	if page.Offset > 0 || page.Returned < count {
		if n.Incomplete == "" {
			n.Incomplete = "pagination"
		}
	}
	return n, nil
}

type DiffKind string

const (
	DiffAdded     DiffKind = "added"
	DiffRemoved   DiffKind = "removed"
	DiffChanged   DiffKind = "changed"
	DiffUnchanged DiffKind = "unchanged"
	DiffUnknown   DiffKind = "unknown"
)

func CompareNodes(before, after *InspectNode) DiffKind {
	if before != nil && !before.IsComplete() || after != nil && !after.IsComplete() {
		return DiffUnknown
	}
	if before == nil && after != nil {
		return DiffAdded
	}
	if before != nil && after == nil {
		return DiffRemoved
	}
	if before == nil {
		return DiffUnknown
	}
	if reflect.DeepEqual(before, after) {
		return DiffUnchanged
	}
	return DiffChanged
}
func Diff(before, after InspectResult) DiffKind {
	if !reflect.DeepEqual(before.Schema, after.Schema) || !reflect.DeepEqual(before.Path, after.Path) {
		return DiffUnknown
	}
	return CompareNodes(&before.Node, &after.Node)
}

func cloneSegment(p Segment) Segment {
	p.Kind = strings.Clone(p.Kind)
	p.Name = strings.Clone(p.Name)
	p.KeyType = strings.Clone(p.KeyType)
	p.Decimal = strings.Clone(p.Decimal)
	return p
}
func cloneInspectSource(s *SourceSite) *SourceSite {
	if s == nil {
		return nil
	}
	copy := *s
	copy.File = strings.Clone(copy.File)
	return &copy
}
func (c *InspectContext) source(s *SourceSite) (*SourceSite, error) {
	if s == nil {
		return nil, nil
	}
	if uint64(len(s.File)) > c.limits.MaxValueBytes || !c.charge(32+uint64(len(s.File))) {
		return nil, fmt.Errorf("source metadata budget")
	}
	copy := *s
	copy.File = strings.Clone(copy.File)
	return &copy, nil
}

// Final admission also bounds projections returned by handwritten adapters.
func cloneProjection(root InspectNode, l InspectLimits) (InspectNode, error) {
	var nodes, bytes uint64
	var visit func(InspectNode, uint64) (InspectNode, error)
	visit = func(n InspectNode, depth uint64) (InspectNode, error) {
		if nodes >= l.MaxNodes || depth > l.MaxDepth {
			return InspectNode{}, fmt.Errorf("projection structural budget")
		}
		nodes++
		size := inspectNodeHeader + uint64(len(n.Name)+len(n.VariantID)+len(n.VariantLabel)+len(n.Text)+len(n.Bytes)+len(n.Ordering)+len(n.Incomplete))
		if n.Page != nil {
			size += 32
		}
		if n.ChildCount != nil {
			size += 8
		}
		if size > l.MaxBytes-bytes {
			return InspectNode{}, fmt.Errorf("projection byte budget")
		}
		bytes += size
		if uint64(len(n.Text)+len(n.Bytes)+len(n.Name)+len(n.VariantID)+len(n.VariantLabel)) > l.MaxValueBytes {
			return InspectNode{}, fmt.Errorf("projection selected-value budget")
		}
		out := n
		out.engineExhausted = false
		out.Name = strings.Clone(n.Name)
		out.VariantID = strings.Clone(n.VariantID)
		out.VariantLabel = strings.Clone(n.VariantLabel)
		out.Text = strings.Clone(n.Text)
		out.Bytes = append([]byte{}, n.Bytes...)
		out.Children = nil
		if n.Scalar != nil {
			x := *n.Scalar
			size := uint64(64 + len(x.Kind) + len(x.Decimal) + len(x.Text))
			if size > l.MaxBytes-bytes || uint64(len(x.Kind)+len(x.Decimal)+len(x.Text)) > l.MaxValueBytes {
				return InspectNode{}, fmt.Errorf("scalar projection budget")
			}
			bytes += size
			if e := validateScalar(x); e != nil {
				return InspectNode{}, e
			}
			x.Kind = strings.Clone(x.Kind)
			x.Decimal = strings.Clone(x.Decimal)
			x.Text = strings.Clone(x.Text)
			out.Scalar = &x
		}
		if n.ChildCount != nil {
			x := *n.ChildCount
			out.ChildCount = &x
		}
		if n.Page != nil {
			x := *n.Page
			if x.NextOffset != nil {
				next := *x.NextOffset
				x.NextOffset = &next
			}
			out.Page = &x
		}
		for _, child := range n.Children {
			size := inspectChildHeader + uint64(len(child.Label)+len(child.Segment.Kind)+len(child.Segment.Name)+len(child.Segment.KeyType)+len(child.Segment.Decimal))
			if uint64(len(child.Label)+len(child.Segment.Kind)+len(child.Segment.Name)+len(child.Segment.KeyType)+len(child.Segment.Decimal)) > l.MaxValueBytes {
				return InspectNode{}, fmt.Errorf("child metadata value limit")
			}
			if child.Source != nil {
				if uint64(len(child.Source.File)) > l.MaxValueBytes {
					return InspectNode{}, fmt.Errorf("source metadata value limit")
				}
				size += 32 + uint64(len(child.Source.File))
			}
			if size > l.MaxBytes-bytes {
				return InspectNode{}, fmt.Errorf("child metadata budget")
			}
			bytes += size
			copied, e := visit(child.Node, depth+1)
			if e != nil {
				return InspectNode{}, e
			}
			c := child
			c.Node = copied
			c.Segment = cloneSegment(child.Segment)
			c.Label = strings.Clone(child.Label)
			if child.Source != nil {
				source := *child.Source
				source.File = strings.Clone(source.File)
				c.Source = &source
			}
			out.Children = append(out.Children, c)
		}
		return out, nil
	}
	// A truncation sentinel represents exhausted work and is still a finite result.
	if root.Kind == "truncated" {
		// Adapter-supplied nodes are untrusted. This sentinel bypasses the
		// ordinary projection budget, so cap its retained reason independently.
		if len(root.Incomplete) > 32 {
			return InspectNode{}, fmt.Errorf("truncation reason limit")
		}
		return InspectNode{Kind: "truncated", Incomplete: strings.Clone(root.Incomplete)}, nil
	}
	return visit(root, 0)
}

func (c *InspectContext) Enum(path []Segment, name, variantID, label string, fields []InspectField) (InspectNode, error) {
	if len(path) > 0 {
		if path[0].Kind != "variant" || path[0].Name != variantID {
			return InspectNode{}, fmt.Errorf("variant path not found")
		}
		return c.Object(path[1:], label, fields)
	}
	if !utf8.ValidString(variantID) || !utf8.ValidString(label) {
		return InspectNode{}, fmt.Errorf("invalid enum metadata")
	}
	if uint64(len(variantID)+len(label)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(uint64(len(variantID) + len(label))) {
		return engineTruncated("bytes"), nil
	}
	variant := InspectField{Segment: Segment{Kind: "variant", Name: variantID}, Label: label, Value: func(rest []Segment, cx *InspectContext) (InspectNode, error) {
		old := cx.offset
		cx.offset = 0
		defer func() { cx.offset = old }()
		return cx.Object(rest, label, fields)
	}}
	node, e := c.container(nil, "enum", name, "", []InspectField{variant}, false)
	if e != nil || node.Kind != "enum" {
		return node, e
	}
	node.VariantID = strings.Clone(variantID)
	node.VariantLabel = strings.Clone(label)
	return node, nil
}
func (c *InspectContext) Opaque(path []Segment, label string) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	node, ok := c.node("opaque")
	if !ok {
		return node, nil
	}
	// Go strings may contain invalid UTF-8 even after the retained preview.
	// Validate the entire label without allocating a copy, charging each rune
	// against work so an untrusted label cannot cause an unbounded scan.
	end := 0
	for i := 0; i < len(label); {
		if !c.spend() {
			return engineTruncated("work"), nil
		}
		r, size := utf8.DecodeRuneInString(label[i:])
		if r == utf8.RuneError && size == 1 {
			return InspectNode{}, fmt.Errorf("invalid opaque label UTF-8")
		}
		i += size
		if uint64(i) <= c.limits.MaxValueBytes {
			end = i
		}
	}
	if !c.charge(uint64(end)) {
		return engineTruncated("bytes"), nil
	}
	node.Name = strings.Clone(label[:end])
	node.Incomplete = "opaque"
	return node, nil
}
func (c *InspectContext) Unavailable(path []Segment) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	node, ok := c.node("unavailable")
	if !ok {
		return node, nil
	}
	node.Incomplete = "unavailable"
	return node, nil
}

type InspectDiff struct {
	Before, After                         SnapshotID
	Path                                  []Segment
	Kind                                  DiffKind
	BeforeCompleteness, AfterCompleteness string
}

func StructuralDiff(before, after InspectResult) InspectDiff {
	path := make([]Segment, len(after.Path))
	for i, p := range after.Path {
		path[i] = cloneSegment(p)
	}
	return InspectDiff{before.Snapshot, after.Snapshot, path, Diff(before, after), before.Node.Incomplete, after.Node.Incomplete}
}

// DiffBounded admits handwritten/public projections under explicit structural
// limits before comparison; ordinary Diff expects already admitted projections.
func DiffBounded(before, after InspectResult, limits InspectLimits) (InspectDiff, error) {
	for _, r := range []InspectResult{before, after} {
		metadata := inspectResultHeader + uint64(len(r.Schema.Name))
		if r.Schema.Source != nil {
			metadata += 32 + uint64(len(r.Schema.Source.File))
		}
		if uint64(len(r.Path)) > limits.MaxDepth {
			return InspectDiff{}, fmt.Errorf("diff path depth budget")
		}
		for _, p := range r.Path {
			metadata += segmentHeader + uint64(len(p.Kind)+len(p.Name)+len(p.KeyType)+len(p.Decimal))
		}
		if metadata > limits.MaxBytes {
			return InspectDiff{}, fmt.Errorf("diff metadata budget")
		}
		admission := limits
		admission.MaxBytes -= metadata
		if _, e := cloneProjection(r.Node, admission); e != nil {
			return InspectDiff{}, e
		}
	}
	return StructuralDiff(before, after), nil
}

func validateScalar(v Scalar) error {
	invalid := func() error { return fmt.Errorf("invalid typed display scalar %q", v.Kind) }
	switch v.Kind {
	case "unit":
		if v.Decimal != "" || v.Text != "" || v.Bits != 0 || v.Bool {
			return invalid()
		}
		return nil
	case "bool":
		if v.Decimal != "" || v.Text != "" || v.Bits != 0 {
			return invalid()
		}
		return nil
	case "char":
		if !utf8.ValidString(v.Text) || utf8.RuneCountInString(v.Text) != 1 || v.Decimal != "" || v.Bits != 0 || v.Bool {
			return invalid()
		}
		return nil
	case "f32", "f64":
		// Rust stores float display text and bits independently, without parsing.
		if v.Text != "" || v.Bool || !utf8.ValidString(v.Decimal) {
			return invalid()
		}
		return nil
	case "float":
		// Text carries Rust's float-kind identity for nonstandard kinds. Keep
		// f32/f64 canonical so equality agrees with the legacy representation.
		if v.Bool || v.Text == "f32" || v.Text == "f64" || !utf8.ValidString(v.Text) || !utf8.ValidString(v.Decimal) {
			return invalid()
		}
		return nil
	case "i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize":
		// Decimal is a UTF-8 identity string, not a parsed number.
		if v.Text != "" || v.Bool || v.Bits != 0 || !utf8.ValidString(v.Decimal) {
			return invalid()
		}
		return nil
	default:
		return invalid()
	}
}
