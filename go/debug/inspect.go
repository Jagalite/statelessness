package debug

import (
	"fmt"
	"math"
	"math/big"
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
type InspectNode struct {
	Kind, Name, VariantID, VariantLabel, Text, Ordering, Incomplete string
	Scalar                                                          *Scalar
	Bytes                                                           []byte
	TotalBytes                                                      uint64
	Children                                                        []ChildNode
	ChildCount                                                      *uint64
	Page                                                            *PageInfo
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

func (n InspectNode) IsComplete() bool {
	if n.Incomplete != "" {
		return false
	}
	switch n.Kind {
	case "scalar":
		if n.Scalar == nil {
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
	for _, c := range n.Children {
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
	r := InspectResult{Snapshot: q.Snapshot}
	if value == nil {
		return r, fmt.Errorf("nil inspect adapter")
	}
	r.Schema = value.DisplaySchema()
	if uint64(len(r.Schema.Name)) > q.Limits.MaxValueBytes {
		return r, fmt.Errorf("schema metadata value limit")
	}
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
	if !cx.charge(inspectResultHeader + uint64(len(r.Schema.Name))) {
		return r, fmt.Errorf("schema metadata byte limit")
	}
	r.Schema.Name = strings.Clone(r.Schema.Name)
	if r.Schema.Source != nil {
		source, e := cx.source(r.Schema.Source)
		if e != nil {
			return r, e
		}
		r.Schema.Source = source
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
		r.Path[i] = cloneSegment(p)
	}
	metadataBytes := cx.bytes
	node, e := value.Inspect(q.Path, cx)
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
func (c *InspectContext) node(kind string) (InspectNode, bool) {
	if !c.spend() {
		return InspectNode{Kind: "truncated", Incomplete: "work"}, false
	}
	if c.depth > c.limits.MaxDepth {
		return InspectNode{Kind: "truncated", Incomplete: "depth"}, false
	}
	if c.nodes >= c.limits.MaxNodes {
		return InspectNode{Kind: "truncated", Incomplete: "nodes"}, false
	}
	if !c.charge(inspectNodeHeader) {
		return InspectNode{Kind: "truncated", Incomplete: "bytes"}, false
	}
	c.nodes++
	return InspectNode{Kind: kind}, true
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
		return InspectNode{Kind: "truncated", Incomplete: "bytes"}, nil
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
	for maximum > 0 && maximum < uint64(len(value)) && !utf8.RuneStart(value[maximum]) {
		maximum--
	}
	if !utf8.ValidString(value[:maximum]) {
		return InspectNode{}, fmt.Errorf("invalid string preview UTF-8")
	}
	n.Text = strings.Clone(value[:maximum])
	if !c.charge(maximum) {
		return InspectNode{}, fmt.Errorf("budget exceeded")
	}
	if maximum < n.TotalBytes {
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
	return c.container(path, "object", name, "", fields)
}
func (c *InspectContext) Sequence(path []Segment, name string, fields []InspectField) (InspectNode, error) {
	return c.container(path, "sequence", name, "", fields)
}
func (c *InspectContext) Map(path []Segment, ordering string, fields []InspectField) (InspectNode, error) {
	if ordering != "stable" && ordering != "unstable" {
		return InspectNode{}, fmt.Errorf("map ordering declaration required")
	}
	return c.container(path, "map", "", ordering, fields)
}
func (c *InspectContext) field(f InspectField, path []Segment) (InspectNode, error) {
	if f.Redacted {
		n, _ := c.node("redacted")
		n.Incomplete = "redacted"
		return n, nil
	}
	if f.Value == nil {
		n, _ := c.node("unavailable")
		n.Incomplete = "unavailable"
		return n, nil
	}
	c.depth++
	defer func() { c.depth-- }()
	return f.Value(path, c)
}
func (c *InspectContext) container(path []Segment, kind, name, ordering string, fields []InspectField) (InspectNode, error) {
	if len(path) > 0 {
		for _, f := range fields {
			if !c.spend() {
				return InspectNode{Kind: "truncated", Incomplete: "work"}, nil
			}
			if f.Segment == path[0] {
				return c.field(f, path[1:])
			}
		}
		return InspectNode{}, fmt.Errorf("path not found")
	}
	n, ok := c.node(kind)
	if !ok {
		return n, nil
	}
	if uint64(len(name)) > c.limits.MaxValueBytes {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	if !c.charge(uint64(len(name))) {
		return InspectNode{Kind: "truncated", Incomplete: "bytes"}, nil
	}
	n.Name = strings.Clone(name)
	n.Ordering = ordering
	if !c.charge(40) {
		return InspectNode{Kind: "truncated", Incomplete: "bytes"}, nil
	}
	count := uint64(len(fields))
	n.ChildCount = &count
	page := PageInfo{Offset: min(c.offset, count), Total: count}
	end := page.Offset + min(c.limit, count-page.Offset)
	for i := uint64(0); i < end; i++ {
		if !c.spend() {
			n.Incomplete = "work"
			break
		}
		if i < page.Offset {
			continue
		}
		f := fields[i]
		if uint64(len(f.Label)+len(f.Segment.Name)+len(f.Segment.Kind)+len(f.Segment.KeyType)+len(f.Segment.Decimal)) > c.limits.MaxValueBytes {
			n.Incomplete = "value_limit"
			break
		}
		if !c.charge(inspectChildHeader + uint64(len(f.Label)+len(f.Segment.Kind)+len(f.Segment.Name)+len(f.Segment.KeyType)+len(f.Segment.Decimal))) {
			n.Incomplete = "bytes"
			break
		}
		child, e := c.field(f, nil)
		if e != nil {
			return InspectNode{}, e
		}
		source, e := c.source(f.Source)
		if e != nil {
			n.Incomplete = "bytes"
			break
		}
		n.Children = append(n.Children, ChildNode{cloneSegment(f.Segment), strings.Clone(f.Label), source, child})
		if child.Kind == "truncated" {
			n.Incomplete = child.Incomplete
			break
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
		return InspectNode{Kind: "truncated", Incomplete: strings.Clone(root.Incomplete)}, nil
	}
	return visit(root, 0)
}

func (c *InspectContext) Enum(path []Segment, name, variantID, label string, fields []InspectField) (InspectNode, error) {
	if len(path) > 0 && path[0].Kind == "variant" {
		if path[0].Name != variantID {
			return InspectNode{}, fmt.Errorf("variant path not found")
		}
		path = path[1:]
	}
	node, e := c.container(path, "enum", name, "", fields)
	if e != nil || node.Kind != "enum" {
		return node, e
	}
	if uint64(len(variantID)+len(label)) > c.limits.MaxValueBytes || !c.charge(uint64(len(variantID)+len(label))) {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
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
	if uint64(len(label)) > c.limits.MaxValueBytes || !c.charge(uint64(len(label))) {
		return InspectNode{Kind: "truncated", Incomplete: "value_limit"}, nil
	}
	node.Name = strings.Clone(label)
	node.Incomplete = "opaque"
	return node, nil
}
func (c *InspectContext) Unavailable(path []Segment) (InspectNode, error) {
	if len(path) > 0 {
		return InspectNode{}, fmt.Errorf("path not found")
	}
	node, _ := c.node("unavailable")
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
		if v.Text != "" || v.Bool || v.Kind == "f32" && v.Bits > math.MaxUint32 {
			return invalid()
		}
		text := v.Decimal
		if text == "inf" {
			text = "+Inf"
		}
		if text == "-inf" {
			text = "-Inf"
		}
		bits := 64
		if v.Kind == "f32" {
			bits = 32
		}
		if _, e := strconv.ParseFloat(text, bits); e != nil {
			return invalid()
		}
		return nil
	}
	signed := false
	bits := 0
	switch v.Kind {
	case "i8":
		signed = true
		bits = 8
	case "i16":
		signed = true
		bits = 16
	case "i32":
		signed = true
		bits = 32
	case "i64":
		signed = true
		bits = 64
	case "i128":
		signed = true
		bits = 128
	case "isize":
		signed = true
		bits = strconv.IntSize
	case "u8":
		bits = 8
	case "u16":
		bits = 16
	case "u32":
		bits = 32
	case "u64":
		bits = 64
	case "u128":
		bits = 128
	case "usize":
		bits = strconv.IntSize
	default:
		return invalid()
	}
	if v.Text != "" || v.Bool || v.Bits != 0 || len(v.Decimal) == 0 || len(v.Decimal) > 40 {
		return invalid()
	}
	number, ok := new(big.Int).SetString(v.Decimal, 10)
	if !ok {
		return invalid()
	}
	if signed {
		bound := new(big.Int).Lsh(big.NewInt(1), uint(bits-1))
		if number.Cmp(new(big.Int).Neg(bound)) < 0 || number.Cmp(bound) >= 0 {
			return invalid()
		}
	} else if number.Sign() < 0 || number.BitLen() > bits {
		return invalid()
	}
	return nil
}
