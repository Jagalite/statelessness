package debug

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"reflect"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
	"unsafe"
)

type WatchAuthorization struct{ paths [][]Segment }

func DenyWatches() WatchAuthorization { return WatchAuthorization{} }
func AllowWatchPaths(paths [][]Segment) WatchAuthorization {
	a := WatchAuthorization{}
	for _, p := range paths {
		a.paths = append(a.paths, append([]Segment{}, p...))
	}
	return a
}
func AllowAllWatches() WatchAuthorization { return AllowWatchPaths([][]Segment{{}}) }
func (a WatchAuthorization) allows(path []Segment) bool {
	for _, p := range a.paths {
		if len(p) <= len(path) && watchPathPrefixEqual(p, path) {
			return true
		}
	}
	return false
}

// Compare the active variant fields, as Rust's typed PathSegment does. Avoid
// decoding or scanning text here: configuration bounds are checked below.
func watchPathPrefixEqual(prefix, path []Segment) bool {
	for i, left := range prefix {
		right := path[i]
		if left.Kind != right.Kind {
			return false
		}
		switch left.Kind {
		case "field", "variant":
			if left.Name != right.Name {
				return false
			}
		case "index":
			if left.Index != right.Index {
				return false
			}
		case "map_key":
			if left.KeyType != right.KeyType {
				return false
			}
			switch left.KeyType {
			case "string", "char":
				if left.Name != right.Name {
					return false
				}
			default:
				if left.Decimal != right.Decimal {
					return false
				}
			}
		default:
			return false
		}
	}
	return true
}

type WatchSelector struct {
	Path                                                []Segment
	SchemaVersion                                       uint32
	Model, Machine, InputVariant, OutputVariant, Origin string
}
type UInt128 struct{ Hi, Lo uint64 }

func (n UInt128) big() *big.Int {
	value := new(big.Int).SetUint64(n.Hi)
	value.Lsh(value, 64)
	return value.Or(value, new(big.Int).SetUint64(n.Lo))
}

type WatchConfig struct {
	Threshold, Hysteresis                 UInt128
	CooldownTurns                         uint64
	Selector                              WatchSelector
	Trigger                               string
	Equals                                *Scalar
	Enabled                               bool
	EvaluationInterval, DeliveryInterval  uint64
	BaselineTTLSequences, TTLObservations *uint64
	Limits                                InspectLimits
	Offset, Limit                         uint64
}

func ChangedWatch(path []Segment) WatchConfig {
	limits := DefaultInspectLimits()
	limits.MaxBytes = 32 * 1024
	return WatchConfig{Selector: WatchSelector{Path: append([]Segment{}, path...), SchemaVersion: 1}, Trigger: "changed", Enabled: true, EvaluationInterval: 1, DeliveryInterval: 1, Limits: limits, Limit: 100}
}

type WatchLimits struct {
	MaxWatches, MaxRingRecords                                    int
	MaxRingBytes, MaxEventBytes, MaxBaselineBytes, MaxConfigBytes uint64
}

func DefaultWatchLimits() WatchLimits {
	return WatchLimits{64, 1000, 4 * 1024 * 1024, 32 * 1024, 8 * 1024 * 1024, 4096}
}

type WatchContext struct {
	FailingProperties []string
	// OutputVariants lists outputs from the transition; OutputVariant remains
	// available for callers with a single output. Selectors match either field.
	OutputVariants                                      []string
	Phase                                               string
	PropertyStatusKnown                                 bool
	Snapshot                                            SnapshotID
	Model, Machine, InputVariant, OutputVariant, Origin string
	PropertyFailed                                      bool
}
type WatchAck struct{ WatchID, Generation, CaptureRevision, EffectiveSequence uint64 }

// DiagnosticEventMetadata records producer-owned provenance; it does not imply
// a complete scoped-turn envelope.
type DiagnosticEventMetadata struct {
	RunID, Producer, Epoch, ProducerSequence, Transition uint64
	Origin, Site                                         string
	Path                                                 []Segment
	Severity                                             Severity
	Kind, BaselineReason                                 string
	SchemaVersion                                        uint32
	Baseline, ChangeUnknown, SchemaKnown                 bool
	TurnCompletenessKnown, IncompleteTurn                bool
}
type WatchEvent struct {
	Diagnostic                                     *DiagnosticEventMetadata
	FailureBaseline, FailureSchemaKnown            bool
	FailureBaselineReason                          string
	InspectionError                                string
	Snapshot                                       SnapshotID
	PreviousSnapshot                               *SnapshotID
	Origin, Phase, SchemaName, Completeness        string
	Path                                           []Segment
	Source                                         *SourceSite
	FailingProperties                              []string
	PropertyFailed, PropertyStatusKnown            bool
	WatchID, Generation, CaptureRevision, Sequence uint64
	Kind                                           string
	Diff                                           DiffKind
	Node                                           *InspectNode
	Dropped                                        uint64
}
type watchSource struct {
	Session                uint64
	Model, Machine, Origin string
}
type watchEntry struct {
	validatedSchema                                                               *DisplaySchema
	current                                                                       *WatchEvent
	currentBytes                                                                  uint64
	effectiveSequence                                                             uint64
	pendingBaseline                                                               string
	source                                                                        watchSource
	high                                                                          bool
	lastAlert                                                                     *uint64
	config                                                                        WatchConfig
	generation, selected, evaluated, delivered, baselineSequence, baselineSession uint64
	baseline                                                                      *InspectResult
	baselineBytes                                                                 uint64
	expired                                                                       bool
}
type WatchHealth struct{ Records, Bytes, DroppedTotal, InspectionFailures, ExporterFailures uint64 }
type WatchRegistry struct {
	currentBytes                                                                               uint64
	observed                                                                                   bool
	observationGaps, droppedTotal, inspectionFailures, exporterFailures                        uint64
	baselineEvictions                                                                          uint64
	limits                                                                                     WatchLimits
	auth                                                                                       WatchAuthorization
	entries                                                                                    map[uint64]*watchEntry
	next, revision, lastSequence, lastSession, lastRevision, ringBytes, baselineBytes, dropped uint64
	ring                                                                                       []WatchEvent
	busy                                                                                       bool
	diagnosticProjection                                                                       bool
}

func NewWatchRegistry(limits WatchLimits, auth WatchAuthorization) (*WatchRegistry, error) {
	if limits.MaxWatches < 0 || limits.MaxRingRecords < 0 {
		return nil, fmt.Errorf("negative watch capacity")
	}
	return &WatchRegistry{limits: limits, auth: auth, entries: map[uint64]*watchEntry{}, next: 1}, nil
}
func cloneWatchConfig(c WatchConfig) WatchConfig {
	c.Selector.Path = append([]Segment{}, c.Selector.Path...)
	for i, p := range c.Selector.Path {
		c.Selector.Path[i] = cloneSegment(p)
	}
	c.Selector.Model = strings.Clone(c.Selector.Model)
	c.Selector.Machine = strings.Clone(c.Selector.Machine)
	c.Selector.InputVariant = strings.Clone(c.Selector.InputVariant)
	c.Selector.OutputVariant = strings.Clone(c.Selector.OutputVariant)
	c.Selector.Origin = strings.Clone(c.Selector.Origin)
	if c.Equals != nil {
		x := *c.Equals
		c.Equals = &x
	}
	if c.BaselineTTLSequences != nil {
		x := *c.BaselineTTLSequences
		c.BaselineTTLSequences = &x
	}
	if c.TTLObservations != nil {
		x := *c.TTLObservations
		c.TTLObservations = &x
	}
	return c
}
func (r *WatchRegistry) validate(c WatchConfig) error {
	if r.observed && r.lastSequence == math.MaxUint64 {
		return fmt.Errorf("revision exhausted")
	}
	if c.Selector.SchemaVersion == 0 || c.EvaluationInterval == 0 || c.DeliveryInterval == 0 || c.Limit == 0 || c.Limit > c.Limits.MaxPageSize || (!r.diagnosticProjection && (c.Limits.MaxDepth > 32 || uint64(len(c.Selector.Path)) > c.Limits.MaxDepth || c.Limits.MaxBytes == 0 || c.Limits.MaxBytes > r.limits.MaxEventBytes)) || (c.TTLObservations != nil && *c.TTLObservations == 0) {
		return fmt.Errorf("invalid watch config")
	}
	if c.Trigger != "changed" && c.Trigger != "every" && c.Trigger != "equals" && c.Trigger != "property_failure" && c.Trigger != "above_u128" {
		return fmt.Errorf("unknown watch trigger")
	}
	if c.Trigger == "equals" && c.Equals == nil {
		return fmt.Errorf("equals scalar required")
	}
	cost := uint64(256 + len(c.Selector.Model) + len(c.Selector.Machine) + len(c.Selector.InputVariant) + len(c.Selector.OutputVariant) + len(c.Selector.Origin))
	if uint64(len(c.Selector.Path)) > r.limits.MaxConfigBytes/64 {
		return fmt.Errorf("watch configuration budget")
	}
	for _, p := range c.Selector.Path {
		cost += 64 + uint64(len(p.Kind)+len(p.Name)+len(p.KeyType)+len(p.Decimal))
	}
	if c.Equals != nil {
		cost += 64 + uint64(len(c.Equals.Kind)+len(c.Equals.Decimal)+len(c.Equals.Text))
	}
	if cost > r.limits.MaxConfigBytes {
		return fmt.Errorf("watch configuration budget")
	}
	for _, segment := range c.Selector.Path {
		if _, ok := canonicalInspectSegment(segment); !ok {
			return fmt.Errorf("invalid watch path")
		}
	}
	if !r.auth.allows(c.Selector.Path) {
		return fmt.Errorf("unauthorized watch path")
	}
	if c.Equals != nil {
		if e := validateScalar(*c.Equals); e != nil {
			return e
		}
	}
	b, e := json.Marshal(c)
	if e != nil || uint64(len(b)) > r.limits.MaxConfigBytes {
		return fmt.Errorf("watch configuration budget")
	}
	return nil
}
func (r *WatchRegistry) Add(c WatchConfig) (WatchAck, error) {
	if r.busy {
		return WatchAck{}, fmt.Errorf("watch callback active")
	}
	if e := r.validate(c); e != nil {
		return WatchAck{}, e
	}
	if len(r.entries) >= r.limits.MaxWatches || r.next == math.MaxUint64 || r.revision == math.MaxUint64 {
		return WatchAck{}, fmt.Errorf("watch capacity or revision exhausted")
	}
	id := r.next
	r.next++
	r.revision++
	r.entries[id] = &watchEntry{config: cloneWatchConfig(c), generation: 1, effectiveSequence: r.boundary()}
	r.push(WatchEvent{WatchID: id, Generation: 1, CaptureRevision: r.revision, Sequence: r.lastSequence, Kind: "watch_enabled"})
	return WatchAck{id, 1, r.revision, r.boundary()}, nil
}
func (r *WatchRegistry) Update(id uint64, c WatchConfig) (WatchAck, error) {
	if r.busy {
		return WatchAck{}, fmt.Errorf("watch callback active")
	}
	w, ok := r.entries[id]
	if !ok {
		return WatchAck{}, fmt.Errorf("unknown watch")
	}
	if e := r.validate(c); e != nil {
		return WatchAck{}, e
	}
	if r.revision == math.MaxUint64 || w.generation == math.MaxUint64 {
		return WatchAck{}, fmt.Errorf("revision exhausted")
	}
	r.currentBytes -= w.currentBytes
	w.currentBytes = 0
	w.current = nil
	r.baselineBytes -= w.baselineBytes
	r.revision++
	generation := w.generation + 1
	reason := "initial"
	if !w.config.Enabled && c.Enabled {
		reason = "reenabled"
	}
	r.entries[id] = &watchEntry{config: cloneWatchConfig(c), generation: generation, pendingBaseline: reason, effectiveSequence: r.boundary()}
	kind := "watch_enabled"
	if !c.Enabled {
		kind = "watch_disabled"
	}
	r.push(WatchEvent{WatchID: id, Generation: generation, CaptureRevision: r.revision, Sequence: r.lastSequence, Kind: kind})
	return WatchAck{id, generation, r.revision, r.boundary()}, nil
}
func (r *WatchRegistry) Remove(id uint64) error {
	if r.busy {
		return fmt.Errorf("watch callback active")
	}
	w, ok := r.entries[id]
	if !ok {
		return fmt.Errorf("unknown watch")
	}
	if r.revision == math.MaxUint64 || w.generation == math.MaxUint64 || r.observed && r.lastSequence == math.MaxUint64 {
		return fmt.Errorf("revision exhausted")
	}
	r.revision++
	r.currentBytes -= w.currentBytes
	w.currentBytes = 0
	w.current = nil
	r.baselineBytes -= w.baselineBytes
	delete(r.entries, id)
	r.push(WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision, Sequence: r.lastSequence, Kind: "watch_removed"})
	return nil
}
func (r *WatchRegistry) CaptureRevision() uint64 { return r.revision }
func matchesWatch(s WatchSelector, c WatchContext) bool {
	return (s.Model == "" || s.Model == c.Model) && (s.Machine == "" || s.Machine == c.Machine) && (s.InputVariant == "" || s.InputVariant == c.InputVariant) && (s.OutputVariant == "" || s.OutputVariant == c.OutputVariant || slices.Contains(c.OutputVariants, s.OutputVariant)) && (s.Origin == "" || s.Origin == c.Origin)
}

// Copy before callbacks and bound host work; caller owns its initial allocation.
func copyWatchOutputs(values []string) ([]string, error) {
	const maxOutputVariants, maxOutputVariantBytes = 4096, 1024 * 1024
	if len(values) > maxOutputVariants {
		return nil, fmt.Errorf("watch output variants budget")
	}
	outputBytes := 0
	outputs := make([]string, len(values))
	for i, output := range values {
		if !utf8.ValidString(output) || len(output) > maxOutputVariantBytes-outputBytes {
			return nil, fmt.Errorf("invalid or oversized watch output variants")
		}
		outputBytes += len(output)
		outputs[i] = strings.Clone(output)
	}
	return outputs, nil
}
func retainedNode(n InspectNode) uint64 {
	size := inspectNodeHeader + uint64(len(n.Name)+len(n.VariantID)+len(n.VariantLabel)+len(n.Text)+len(n.Bytes)+len(n.Ordering)+len(n.Incomplete))
	if n.Page != nil {
		size += 32
	}
	if n.ChildCount != nil {
		size += 8
	}
	if n.Scalar != nil {
		size += uint64(64 + len(n.Scalar.Kind) + len(n.Scalar.Decimal) + len(n.Scalar.Text))
	}
	for _, c := range n.Children {
		if c.Source != nil {
			size += 32 + uint64(len(c.Source.File))
		}
		size += inspectChildHeader + uint64(len(c.Label)+len(c.Segment.Name)+len(c.Segment.Kind)+len(c.Segment.KeyType)+len(c.Segment.Decimal)) + retainedNode(c.Node)
	}
	return size
}
func (r *WatchRegistry) recordDropped() {
	if r.droppedTotal < math.MaxUint64 {
		r.droppedTotal++
		if r.dropped < math.MaxUint64 {
			r.dropped++
		}
	}
}

// reportDrops adds a best-effort marker without evicting existing records.
// Unreported drops remain pending across drains until a later observation has room.
func (r *WatchRegistry) reportDrops() {
	if r.diagnosticProjection || r.dropped == 0 {
		return
	}
	event := WatchEvent{CaptureRevision: r.revision, Snapshot: SnapshotID{Session: r.lastSession, Revision: r.lastRevision, Sequence: r.lastSequence}, Sequence: r.lastSequence, Origin: "diagnostic_control", Phase: "control", Kind: "events_dropped", Completeness: "observation_gap", Diff: DiffUnknown, Dropped: r.dropped}
	size := retainedEvent(event)
	if len(r.ring) >= r.limits.MaxRingRecords || size > r.limits.MaxEventBytes || size > r.limits.MaxRingBytes-r.ringBytes {
		return
	}
	r.ring = append(r.ring, event)
	r.ringBytes += size
	r.dropped = 0
}

func (r *WatchRegistry) push(event WatchEvent) bool {
	// Reject oversized records before allocating owned copies of their metadata.
	bytes := retainedEvent(event)
	if r.limits.MaxRingRecords == 0 || bytes > r.limits.MaxEventBytes || bytes > r.limits.MaxRingBytes {
		r.recordDropped()
		return false
	}
	event, ok := r.ownedEvent(event)
	if !ok {
		r.recordDropped()
		return false
	}
	bytes = retainedEvent(event)
	if bytes > r.limits.MaxEventBytes || bytes > r.limits.MaxRingBytes {
		r.recordDropped()
		return false
	}
	for len(r.ring) >= r.limits.MaxRingRecords || bytes > r.limits.MaxRingBytes-r.ringBytes {
		old := r.ring[0]
		oldBytes := retainedEvent(old)
		r.ringBytes -= oldBytes
		r.ring = r.ring[1:]
		r.recordDropped()
	}
	r.ring = append(r.ring, event)
	r.ringBytes += bytes
	return true
}

// ownedEvent returns a detached deep copy of every host-visible field. It is
// shared by queue admission and non-destructive protocol previews so neither
// path can alias caller or queued storage, or omit a field the other copies.
func (r *WatchRegistry) ownedEvent(event WatchEvent) (WatchEvent, bool) {
	event.Kind = strings.Clone(event.Kind)
	event.FailureBaselineReason = strings.Clone(event.FailureBaselineReason)
	event.InspectionError = strings.Clone(event.InspectionError)
	event.Origin = strings.Clone(event.Origin)
	event.Phase = strings.Clone(event.Phase)
	event.SchemaName = strings.Clone(event.SchemaName)
	event.Completeness = strings.Clone(event.Completeness)
	event.Path = append([]Segment(nil), event.Path...)
	for i := range event.Path {
		event.Path[i] = cloneSegment(event.Path[i])
	}
	event.FailingProperties = append([]string(nil), event.FailingProperties...)
	for i := range event.FailingProperties {
		event.FailingProperties[i] = strings.Clone(event.FailingProperties[i])
	}
	if event.PreviousSnapshot != nil {
		previous := *event.PreviousSnapshot
		event.PreviousSnapshot = &previous
	}
	if event.Diagnostic != nil {
		metadata := *event.Diagnostic
		metadata.Origin = strings.Clone(metadata.Origin)
		metadata.Site = strings.Clone(metadata.Site)
		metadata.Kind = strings.Clone(metadata.Kind)
		metadata.BaselineReason = strings.Clone(metadata.BaselineReason)
		metadata.Path = append([]Segment(nil), metadata.Path...)
		for i := range metadata.Path {
			metadata.Path[i] = cloneSegment(metadata.Path[i])
		}
		event.Diagnostic = &metadata
	}

	if event.Source != nil {
		source := *event.Source
		source.File = strings.Clone(source.File)
		event.Source = &source
	}
	if event.Node != nil {
		node, e := cloneProjection(*event.Node, InspectLimits{MaxWork: r.limits.MaxEventBytes, MaxDepth: 256, MaxNodes: r.limits.MaxEventBytes/inspectNodeHeader + 1, MaxBytes: r.limits.MaxEventBytes, MaxValueBytes: r.limits.MaxEventBytes, MaxPageSize: r.limits.MaxEventBytes})
		if e != nil {
			return WatchEvent{}, false
		}
		event.Node = &node
	}
	return event, true
}

// Observe calls value for a selected watch to check its schema, even on sampled boundaries.
// It skips the value and schema callbacks when no enabled watch is selected.
// Projection remains cooperative; it executes no reducer, codec or runtime clock.
func (r *WatchRegistry) Observe(context WatchContext, value func() InspectAdapter) error {
	if r.busy {
		return fmt.Errorf("watch callback active")
	}
	r.busy = true
	defer func() { r.busy = false }()
	outputs, err := copyWatchOutputs(context.OutputVariants)
	if err != nil {
		return err
	}
	context.OutputVariants = outputs
	metadataSize := uint64(len(context.Model) + len(context.Machine) + len(context.Origin) + len(context.InputVariant) + len(context.OutputVariant) + len(context.Phase))
	if uint64(len(context.FailingProperties)) > r.limits.MaxEventBytes/8 {
		return fmt.Errorf("failure metadata budget")
	}
	for _, id := range context.FailingProperties {
		metadataSize += uint64(len(id)) + 16
	}
	if metadataSize > min(r.limits.MaxConfigBytes, r.limits.MaxEventBytes) {
		return fmt.Errorf("watch context metadata budget")
	}
	if !r.diagnosticProjection && r.observed {
		if context.Snapshot.Session == r.lastSession && context.Snapshot.Sequence <= r.lastSequence {
			return nil
		}
		interested := false
		for _, entry := range r.entries {
			if entry.config.Enabled && !entry.expired {
				interested = true
				break
			}
		}
		gap := context.Snapshot.Session != r.lastSession || (r.lastSequence < math.MaxUint64 && context.Snapshot.Sequence != r.lastSequence+1)
		if interested && gap {
			if r.observationGaps < math.MaxUint64 {
				r.observationGaps++
			}
			ids := make([]uint64, 0, len(r.entries))
			for id := range r.entries {
				ids = append(ids, id)
			}
			sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
			for _, id := range ids {
				entry := r.entries[id]
				r.baselineBytes -= entry.baselineBytes
				entry.baselineBytes = 0
				entry.baseline = nil
				r.currentBytes -= entry.currentBytes
				entry.currentBytes = 0
				entry.current = nil
				entry.source = watchSource{}
				entry.pendingBaseline = "observation_gap"
				entry.high = false
				entry.lastAlert = nil
				r.push(WatchEvent{WatchID: id, Generation: entry.generation, CaptureRevision: r.revision, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "observation_gap", Completeness: "observation_gap"})
			}
		}
	}
	r.lastSequence = context.Snapshot.Sequence
	r.lastSession = context.Snapshot.Session
	r.lastRevision = context.Snapshot.Revision
	r.observed = true
	// Rust reports drop markers after a boundary that began with an interested watch.
	interestedAtBoundary := false
	for _, w := range r.entries {
		if w.config.Enabled && !w.expired {
			interestedAtBoundary = true
			break
		}
	}
	// Known property failures force capture, but never bypass watch policy gates.
	propertyFailure := !r.diagnosticProjection && context.PropertyStatusKnown && context.PropertyFailed
	var adapter InspectAdapter
	var adapterErr error
	var adapterSchema DisplaySchema
	adapterEvaluated := false
	ids := make([]uint64, 0, len(r.entries))
	for id := range r.entries {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		w := r.entries[id]
		if w == nil || !w.config.Enabled || w.expired || !matchesWatch(w.config.Selector, context) {
			continue
		}
		if w.selected == math.MaxUint64 || w.evaluated == math.MaxUint64 || w.delivered == math.MaxUint64 {
			return fmt.Errorf("watch counter exhausted")
		}
		if value == nil {
			return fmt.Errorf("selected watch needs value")
		}
		if !adapterEvaluated {
			adapter, adapterErr = watchAdapterSafely(value)
			if adapterErr == nil {
				if adapter == nil {
					adapterErr = fmt.Errorf("nil inspect adapter")
				} else {
					adapterSchema, adapterErr = inspectSchemaSafely(adapter)
					if adapterErr == nil {
						adapter = watchSnapshotAdapter{InspectAdapter: adapter, schema: adapterSchema}
					}
				}
			}
			adapterEvaluated = true
		}
		if adapterErr == nil && (adapterSchema.Version != w.config.Selector.SchemaVersion ||
			(w.validatedSchema != nil && (adapterSchema.Name != w.validatedSchema.Name || adapterSchema.Version != w.validatedSchema.Version))) {
			// A schema identity change pauses this watch before its accessor runs.
			r.currentBytes -= w.currentBytes
			w.currentBytes = 0
			w.current = nil
			r.baselineBytes -= w.baselineBytes
			w.baselineBytes = 0
			w.baseline = nil
			w.source = watchSource{}
			w.pendingBaseline = "observation_gap"
			w.config.Enabled = false
			r.push(WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "schema_changed"})
			continue
		}
		// Record the observed identity before the accessor runs, including when
		// projection later fails. Bound the retained Go string by the projection
		// budget; malformed or oversized host metadata is not retained.
		if adapterErr == nil && w.validatedSchema == nil && utf8.ValidString(adapterSchema.Name) && uint64(len(adapterSchema.Name)) <= w.config.Limits.MaxBytes {
			w.validatedSchema = &DisplaySchema{Name: strings.Clone(adapterSchema.Name), Version: adapterSchema.Version}
		}
		// Preserve a source change observed between projected samples.
		if w.baseline != nil && w.source != (watchSource{context.Snapshot.Session, context.Model, context.Machine, context.Origin}) {
			w.pendingBaseline = "source_changed"
		}
		w.selected++
		event := WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Origin: strings.Clone(context.Origin), Phase: strings.Clone(context.Phase), PropertyFailed: context.PropertyFailed, PropertyStatusKnown: context.PropertyStatusKnown}
		for _, id := range context.FailingProperties {
			event.FailingProperties = append(event.FailingProperties, strings.Clone(id))
		}
		expire := func() {
			if w.config.TTLObservations != nil && w.selected >= *w.config.TTLObservations {
				w.expired = true
				w.config.Enabled = false
				r.push(WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "watch_expired"})
			}
		}
		if !propertyFailure && (w.selected-1)%w.config.EvaluationInterval != 0 {
			expire()
			continue
		}
		q := InspectQuery{Snapshot: context.Snapshot, Path: w.config.Selector.Path, SchemaVersion: w.config.Selector.SchemaVersion, Offset: w.config.Offset, Limit: w.config.Limit, Limits: w.config.Limits}
		result, e := InspectResult{Snapshot: context.Snapshot}, adapterErr
		if e == nil {
			result, e = Inspect(adapter, q)
		}
		w.evaluated++
		if e != nil || (result.Node.Kind == "redacted" && !r.diagnosticProjection) {
			// Preserve only schema and baseline information actually observed
			// before clearing the failed projection's retained state.
			event.FailureBaseline = w.baseline == nil
			if event.FailureBaseline {
				event.FailureBaselineReason = w.pendingBaseline
				if event.FailureBaselineReason == "" {
					event.FailureBaselineReason = "initial"
				}
			}
			if result.Schema.Version == q.SchemaVersion && uint64(len(result.Schema.Name)) <= q.Limits.MaxValueBytes && utf8.ValidString(result.Schema.Name) {
				event.FailureSchemaKnown = true
				event.SchemaName = boundedWatchSchema(result.Schema.Name)
				if w.baseline != nil && (w.baseline.Schema.Name != result.Schema.Name || w.baseline.Schema.Version != result.Schema.Version) {
					event.FailureBaseline = true
					event.FailureBaselineReason = "schema_changed"
				}
			}
			// An unavailable observation breaks continuity, not retained history.
			r.currentBytes -= w.currentBytes
			w.currentBytes = 0
			w.current = nil
			r.baselineBytes -= w.baselineBytes
			w.baselineBytes = 0
			w.baseline = nil
			w.source = watchSource{}
			w.pendingBaseline = "observation_gap"
			if r.diagnosticProjection {
				w.pendingBaseline = "inspection_incomplete"
			}
			event.Diff = DiffUnknown
			if e != nil {
				if r.inspectionFailures < math.MaxUint64 {
					r.inspectionFailures++
				}
				event.Kind = "inspection_error"
				message, _ := safeErrorMessage(e)
				event.InspectionError = normalizedInspectionError(message)
				event.Completeness = "unavailable"
			} else {
				event.Kind = "redacted_path"
				event.Completeness = "redacted"
			}
			size := retainedEvent(event)
			if size <= r.limits.MaxBaselineBytes-r.baselineBytes-r.currentBytes {
				copy := event
				w.current = &copy
				w.currentBytes = size
				r.currentBytes += size
			} else if !r.diagnosticProjection {
				w.pendingBaseline = "evicted"
				if r.baselineEvictions < math.MaxUint64 {
					r.baselineEvictions++
				}
			}
			eligible := w.config.Trigger == "every" || w.config.Trigger == "changed" || propertyFailure
			if !eligible {
				expire()
				continue
			}
			w.delivered++
			if propertyFailure || (w.delivered-1)%w.config.DeliveryInterval == 0 {
				r.push(event)
			}
			expire()
			continue
		}
		reason := ""
		if w.baseline == nil {
			reason = w.pendingBaseline
			w.pendingBaseline = ""
			if reason == "" {
				reason = "initial"
			}
		} else if w.pendingBaseline == "source_changed" {
			reason = w.pendingBaseline
			w.pendingBaseline = ""
		} else if !reflect.DeepEqual(w.baseline.Schema, result.Schema) {
			reason = "schema_changed"
		} else if context.Snapshot.Sequence < w.baselineSequence || context.Snapshot.Sequence > w.baselineSequence && context.Snapshot.Sequence-w.baselineSequence > 1 && w.config.EvaluationInterval == 1 {
			reason = "observation_gap"
		}
		// Rust's TTL is measured from the retained current observation, including
		// an incomplete node or inspection error, not just a complete baseline.
		// If current was evicted, there is no retained timestamp to expire.
		if !r.diagnosticProjection && w.current != nil && w.config.BaselineTTLSequences != nil && context.Snapshot.Sequence >= w.current.Snapshot.Sequence && context.Snapshot.Sequence-w.current.Snapshot.Sequence > *w.config.BaselineTTLSequences {
			reason = "expired"
		} else if r.diagnosticProjection && w.baseline != nil && w.config.BaselineTTLSequences != nil && context.Snapshot.Sequence >= w.baselineSequence && context.Snapshot.Sequence-w.baselineSequence > *w.config.BaselineTTLSequences {
			reason = "expired"
		}
		event.Path = append([]Segment{}, result.Path...)
		event.SchemaName = boundedWatchSchema(result.Schema.Name)
		event.Source = result.Schema.Source
		event.Completeness = result.Node.Incomplete
		if event.Completeness == "" && len(result.Schema.Name) > 1024 {
			event.Completeness = "value_limit"
		}
		if w.baseline != nil {
			x := w.baseline.Snapshot
			event.PreviousSnapshot = &x
		}
		kind := DiffUnknown
		if reason == "" {
			kind = Diff(*w.baseline, result)
		}
		deliver := false
		switch w.config.Trigger {
		case "every":
			deliver = true
		case "changed":
			deliver = reason != "" || kind != DiffUnchanged
		case "equals":
			deliver = result.Node.IsComplete() && reflect.DeepEqual(result.Node.Scalar, w.config.Equals)
		case "property_failure":
			deliver = context.PropertyStatusKnown && context.PropertyFailed
		case "above_u128":
			if reason != "" {
				w.high = false
				w.lastAlert = nil
			}
			if numeric, ok := unsignedProjection(result.Node); ok {
				threshold := w.config.Threshold.big()
				low := new(big.Int).Sub(threshold, w.config.Hysteresis.big())
				if low.Sign() < 0 {
					low.SetUint64(0)
				}
				if numeric.Cmp(low) <= 0 {
					w.high = false
				}
				cool := w.lastAlert == nil || context.Snapshot.Sequence >= *w.lastAlert && context.Snapshot.Sequence-*w.lastAlert >= w.config.CooldownTurns
				if numeric.Cmp(threshold) > 0 && !w.high && cool {
					deliver = true
					w.high = true
					x := context.Snapshot.Sequence
					w.lastAlert = &x
				}
			}
		}
		deliver = deliver || propertyFailure
		if !result.Node.IsComplete() && !r.diagnosticProjection {
			event.Kind = "unknown"
		} else if reason != "" {
			event.Kind = "baseline_" + reason
		} else {
			event.Kind = "watch_value"
			if w.config.EvaluationInterval > 1 {
				event.Kind = "sampled_change"
			}
		}

		r.currentBytes -= w.currentBytes
		w.currentBytes = 0
		w.current = nil
		r.baselineBytes -= w.baselineBytes
		w.baseline = nil
		w.baselineBytes = 0
		w.source = watchSource{}
		size := retainedNode(result.Node) + uint64(128+len(result.Schema.Name)+len(context.Model)+len(context.Machine)+len(context.Origin))
		for _, segment := range result.Path {
			size += uint64(64 + len(segment.Kind) + len(segment.Name) + len(segment.KeyType) + len(segment.Decimal))
		}
		if result.Schema.Source != nil {
			size += 32 + uint64(len(result.Schema.Source.File))
		}
		if !result.Node.IsComplete() {
			w.pendingBaseline = "observation_gap"
			if r.diagnosticProjection {
				w.pendingBaseline = "inspection_incomplete"
			}
		} else if size <= r.limits.MaxBaselineBytes-r.baselineBytes-r.currentBytes {
			copy := result
			w.baseline = &copy
			w.baselineBytes = size
			r.baselineBytes += size
			w.baselineSequence = context.Snapshot.Sequence
			w.baselineSession = context.Snapshot.Session
			w.source = watchSource{context.Snapshot.Session, strings.Clone(context.Model), strings.Clone(context.Machine), strings.Clone(context.Origin)}
		} else {
			w.pendingBaseline = "evicted"
			if r.diagnosticProjection {
				if r.baselineEvictions < math.MaxUint64 {
					r.baselineEvictions++
				}
				w.high = false
				w.lastAlert = nil
			} else {
				event.Kind = "baseline_evicted"
				deliver = true
			}
		}
		if propertyFailure {
			event.Kind = "property_failure"
		}
		// Retain current observations independently of ring sampling, under the
		// same aggregate byte budget as baselines. Never retain a rejected node.
		current := event
		current.Node = &result.Node
		current.Diff = kind
		currentSize := retainedEvent(current)
		if currentSize <= r.limits.MaxBaselineBytes-r.baselineBytes-r.currentBytes {
			w.current = &current
			w.currentBytes = currentSize
			r.currentBytes += currentSize
		} else if !r.diagnosticProjection {
			// A baseline without its current observation cannot represent Rust's
			// retained watch state. Drop both and mark the next observation evicted.
			r.baselineBytes -= w.baselineBytes
			w.baselineBytes = 0
			w.baseline = nil
			w.source = watchSource{}
			w.pendingBaseline = "evicted"
			if r.baselineEvictions < math.MaxUint64 {
				r.baselineEvictions++
			}
		}
		if deliver {
			w.delivered++
			if propertyFailure || (w.delivered-1)%w.config.DeliveryInterval == 0 {
				node := result.Node
				event.Node = &node
				event.Diff = kind
				r.push(event)
			}
		}
		expire()
	}
	if interestedAtBoundary {
		r.reportDrops()
	}
	return nil
}

func watchAdapterSafely(value func() InspectAdapter) (adapter InspectAdapter, err error) {
	defer func() {
		if recover() != nil {
			err = fmt.Errorf("inspection adapter callback panicked")
		}
	}()
	return value(), nil
}
func (r *WatchRegistry) Drain() ([]WatchEvent, error) {
	if r.busy {
		return nil, fmt.Errorf("watch callback active")
	}
	events := r.ring
	if r.diagnosticProjection && r.dropped > 0 {
		events = append([]WatchEvent{{Kind: "events_dropped", Dropped: r.dropped}}, events...)
	}
	r.ring = nil
	r.ringBytes = 0
	if r.diagnosticProjection {
		r.dropped = 0
	}
	return events, nil
}

// Export writes a bounded local queue explicitly. Failure does not mutate the
// model or consume retained events. Retrying can repeat a partially written item.
func (r *WatchRegistry) Export(w io.Writer) (failure error) {
	defer func() {
		if failure != nil {
			r.exporterFailures++
		}
	}()
	if r.busy {
		return fmt.Errorf("watch callback active")
	}
	r.busy = true
	defer func() { r.busy = false }()
	events := r.ring
	if r.diagnosticProjection && r.dropped > 0 {
		events = append([]WatchEvent{{Kind: "events_dropped", Dropped: r.dropped}}, events...)
	}
	for _, e := range events {
		b, err := json.Marshal(e)
		if err != nil {
			return err
		}
		b = append(b, '\n')
		for len(b) > 0 {
			n, err := w.Write(b)
			if n < 0 || n > len(b) {
				return fmt.Errorf("invalid writer count")
			}
			b = b[n:]
			if err != nil {
				return err
			}
			if n == 0 {
				return io.ErrShortWrite
			}
		}
	}
	r.ring = nil
	r.ringBytes = 0
	if r.diagnosticProjection {
		r.dropped = 0
	}
	return nil
}

func (r *WatchRegistry) Health() WatchHealth {
	return WatchHealth{uint64(len(r.ring)), r.ringBytes, r.droppedTotal, r.inspectionFailures, r.exporterFailures}
}

func unsignedProjection(node InspectNode) (*big.Int, bool) {
	if !node.IsComplete() || node.Kind != "scalar" || node.Scalar == nil {
		return nil, false
	}
	bits := 0
	switch node.Scalar.Kind {
	case "u8":
		bits = 8
	case "u16":
		bits = 16
	case "u32":
		bits = 32
	case "u64":
		bits = 64
	case "usize":
		bits = strconv.IntSize
	case "u128":
		bits = 128
	default:
		return nil, false
	}
	value, ok := new(big.Int).SetString(node.Scalar.Decimal, 10)
	if !ok || value.Sign() < 0 || value.BitLen() > bits {
		return nil, false
	}
	return value, true
}

func retainedEvent(event WatchEvent) uint64 {
	size := uint64(unsafe.Sizeof(WatchEvent{})) + uint64(len(event.Kind)+len(event.FailureBaselineReason)+len(event.InspectionError)+len(event.Origin)+len(event.Phase)+len(event.SchemaName)+len(event.Completeness))
	if event.PreviousSnapshot != nil {
		size += uint64(unsafe.Sizeof(SnapshotID{}))
	}
	if event.Node != nil {
		size += retainedNode(*event.Node)
	}
	if event.Diagnostic != nil {
		size += uint64(unsafe.Sizeof(DiagnosticEventMetadata{})) + uint64(len(event.Diagnostic.Origin)+len(event.Diagnostic.Site)+len(event.Diagnostic.Kind)+len(event.Diagnostic.BaselineReason))
		for _, segment := range event.Diagnostic.Path {
			size += segmentHeader + uint64(len(segment.Kind)+len(segment.Name)+len(segment.KeyType)+len(segment.Decimal))
		}
	}
	for _, p := range event.Path {
		size += segmentHeader + uint64(len(p.Kind)+len(p.Name)+len(p.KeyType)+len(p.Decimal))
	}
	if event.Source != nil {
		size += 32 + uint64(len(event.Source.File))
	}
	for _, id := range event.FailingProperties {
		size += 16 + uint64(len(id))
	}
	return size
}

// Gap explicitly invalidates change baselines after an observation was omitted.
func (r *WatchRegistry) Gap(context WatchContext) error {
	if r.busy {
		return fmt.Errorf("watch callback active")
	}
	outputs, err := copyWatchOutputs(context.OutputVariants)
	if err != nil {
		return err
	}
	context.OutputVariants = outputs
	if r.observationGaps < math.MaxUint64 {
		r.observationGaps++
	}
	ids := make([]uint64, 0, len(r.entries))
	for id := range r.entries {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		w := r.entries[id]
		if w.config.Enabled && matchesWatch(w.config.Selector, context) {
			r.currentBytes -= w.currentBytes
			w.currentBytes = 0
			w.current = nil
			r.baselineBytes -= w.baselineBytes
			w.baseline = nil
			w.source = watchSource{}
			w.pendingBaseline = "observation_gap"
			w.baselineBytes = 0
			r.push(WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision, Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Kind: "observation_gap", Completeness: "observation_gap"})
		}
	}
	return nil
}

type ResolvedWatchAck struct {
	WatchAck
	DisplaySchema DisplaySchema
	ResolvedKind  string
}

func (r *WatchRegistry) AddValidated(expectedRevision uint64, config WatchConfig, value InspectAdapter, snapshot SnapshotID) (ResolvedWatchAck, error) {
	if expectedRevision != r.revision {
		return ResolvedWatchAck{}, fmt.Errorf("stale capture revision")
	}
	if e := r.validate(config); e != nil {
		return ResolvedWatchAck{}, e
	}
	if r.busy {
		return ResolvedWatchAck{}, fmt.Errorf("watch callback active")
	}
	if len(r.entries) >= r.limits.MaxWatches || r.next == math.MaxUint64 || r.revision == math.MaxUint64 {
		return ResolvedWatchAck{}, fmt.Errorf("watch capacity or revision exhausted")
	}
	result, e := func() (InspectResult, error) {
		r.busy = true
		defer func() { r.busy = false }()
		return Inspect(value, InspectQuery{Snapshot: snapshot, Path: config.Selector.Path, SchemaVersion: config.Selector.SchemaVersion, Offset: config.Offset, Limit: config.Limit, Limits: config.Limits})
	}()
	if e != nil {
		return ResolvedWatchAck{}, e
	}
	if result.Node.Kind == "redacted" {
		return ResolvedWatchAck{}, fmt.Errorf("redacted watch path")
	}
	ack, e := r.Add(config)
	if e != nil {
		return ResolvedWatchAck{}, e
	}
	schema := DisplaySchema{Name: strings.Clone(result.Schema.Name), Version: result.Schema.Version}
	r.entries[ack.WatchID].validatedSchema = &schema
	kind := result.Node.Kind
	if result.Node.Scalar != nil {
		kind = result.Node.Scalar.Kind
		if kind == "float" {
			kind = result.Node.Scalar.Text
		}
	}
	return ResolvedWatchAck{ack, result.Schema, kind}, nil
}

func (r *WatchRegistry) boundary() uint64 {
	if !r.observed {
		return 0
	}
	if r.lastSequence == math.MaxUint64 {
		return math.MaxUint64
	}
	return r.lastSequence + 1
}

func normalizedInspectionError(message string) string {
	switch message {
	case "path not found", "inspection path not found":
		return "PathNotFound"
	case "wrong path segment", "wrong inspection path segment":
		return "WrongPathSegment"
	case "invalid page":
		return "InvalidPage"
	case "work limit":
		return "WorkLimit"
	case "budget exceeded", "projection budget exceeded", "path budget exceeded":
		return "BudgetExceeded"
	}
	return "InvalidMetadata"
}

// Error formatting belongs to the host callback and can itself panic.
func safeErrorMessage(err error) (message string, ok bool) {
	defer func() {
		if recover() != nil {
			message = ""
			ok = false
		}
	}()
	return err.Error(), true
}

// SnapshotNow projects a watch at an explicit safe point, including when the
// watch is disabled. It does not advance observation order or change its
// baseline. The returned event and ring entry own independent projections.
func (r *WatchRegistry) SnapshotNow(id uint64, value InspectAdapter, context WatchContext) (WatchEvent, error) {
	if r.busy {
		return WatchEvent{}, fmt.Errorf("watch callback active")
	}
	w, ok := r.entries[id]
	if !ok {
		return WatchEvent{}, fmt.Errorf("unknown watch")
	}
	// Unlike configuration updates, a snapshot does not need another sequence.
	if !r.auth.allows(w.config.Selector.Path) {
		return WatchEvent{}, fmt.Errorf("unauthorized watch path")
	}
	if !utf8.ValidString(context.Origin) || len(context.Origin) > 1024 {
		return WatchEvent{}, fmt.Errorf("invalid snapshot origin")
	}
	metadataIncomplete := len(context.FailingProperties) > 16
	ids := make([]string, 0, min(len(context.FailingProperties), 16))
	for _, id := range context.FailingProperties[:min(len(context.FailingProperties), 16)] {
		if !utf8.ValidString(id) {
			return WatchEvent{}, fmt.Errorf("invalid failure property identity")
		}
		end := min(len(id), 256)
		if len(id) > end {
			metadataIncomplete = true
		}
		for end > 0 && end < len(id) && !utf8.RuneStart(id[end]) {
			end--
		}
		ids = append(ids, strings.Clone(id[:end]))
	}
	r.busy = true
	defer func() { r.busy = false }()
	config := w.config
	event := WatchEvent{WatchID: id, Generation: w.generation, CaptureRevision: r.revision,
		Sequence: context.Snapshot.Sequence, Snapshot: context.Snapshot, Origin: strings.Clone(context.Origin),
		Phase: "control", Kind: "snapshot", Diff: DiffUnknown,
		PropertyFailed: context.PropertyFailed, PropertyStatusKnown: context.PropertyStatusKnown,
		FailingProperties: ids, Path: config.Selector.Path}
	// Reserve known event metadata before invoking the adapter. Unknown schema
	// metadata is checked against the completed event before it is returned.
	overhead := retainedEvent(event)
	if overhead >= r.limits.MaxEventBytes {
		return WatchEvent{}, fmt.Errorf("snapshot event budget")
	}
	if value == nil {
		return WatchEvent{}, fmt.Errorf("nil inspect adapter")
	}
	schema, err := inspectSchemaSafely(value)
	if err != nil {
		return WatchEvent{}, err
	}
	if known := w.validatedSchema; known != nil && (known.Name != schema.Name || known.Version != schema.Version) {
		return WatchEvent{}, fmt.Errorf("schema mismatch")
	}
	value = watchSnapshotAdapter{InspectAdapter: value, schema: schema}
	q := InspectQuery{Snapshot: context.Snapshot, Path: config.Selector.Path, SchemaVersion: config.Selector.SchemaVersion,
		Offset: config.Offset, Limit: config.Limit, Limits: config.Limits}
	q.Limits.MaxBytes = min(q.Limits.MaxBytes, r.limits.MaxEventBytes-overhead)
	result, err := Inspect(value, q)
	if err != nil {
		return WatchEvent{}, err
	}
	if result.Node.Kind == "redacted" {
		return WatchEvent{}, fmt.Errorf("redacted watch path")
	}
	event.Path, event.SchemaName, event.Source = result.Path, boundedWatchSchema(result.Schema.Name), result.Schema.Source
	event.Completeness, event.Node = result.Node.Incomplete, &result.Node
	if event.Completeness == "" && (metadataIncomplete || len(result.Schema.Name) > 1024) {
		event.Completeness = "value_limit"
	}
	if retainedEvent(event) > r.limits.MaxEventBytes {
		return WatchEvent{}, fmt.Errorf("snapshot event budget")
	}
	r.push(event)
	return event, nil
}

// Bound event metadata at a UTF-8 boundary while retaining the full schema
// separately for identity comparisons and projection.
func boundedWatchSchema(name string) string {
	end := min(len(name), 1024)
	for end > 0 && end < len(name) && !utf8.RuneStart(name[end]) {
		end--
	}
	return strings.Clone(name[:end])
}

// Freeze the schema checked before projection so a stateful adapter cannot
// return a different identity on a second DisplaySchema call.
type watchSnapshotAdapter struct {
	InspectAdapter
	schema DisplaySchema
}

func (a watchSnapshotAdapter) DisplaySchema() DisplaySchema { return a.schema }
