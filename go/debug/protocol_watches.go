package debug

import (
	"encoding/hex"
	"math"
	"sort"
	"strconv"
	"strings"
)

type protocolWatches[S any] struct {
	registry *WatchRegistry
	project  func(S) InspectAdapter
}

// EnableWatches attaches a dedicated registry. Reconnection preserves its policy
// and baselines; protocol attachment never grants a producer acknowledgement.
func (p *ProtocolSession[S, I, O]) EnableWatches(registry *WatchRegistry, project func(S) InspectAdapter) error {
	if p.busy {
		return ProtocolError("busy")
	}
	if p.watches != nil || registry == nil || project == nil {
		return ProtocolError("invalid_input")
	}
	p.watches = &protocolWatches[S]{registry, project}
	p.configuration = registry.CaptureRevision()
	p.observeWatches()
	return nil
}
func (p *ProtocolSession[S, I, O]) observeWatches() {
	w := p.watches
	if w == nil {
		return
	}
	s := p.session
	old := s.busy
	s.busy = true
	defer func() { s.busy = old }()
	origin := "simulation"
	if s.policy.Unrestricted {
		origin = "out_of_domain_injection"
	}
	_ = w.registry.Observe(WatchContext{Snapshot: SnapshotID{s.Epoch(), s.Revision(), s.Sequence()}, Origin: origin, PropertyStatusKnown: true, PropertyFailed: s.stop != nil && s.stop.Kind == "property_failure"}, func() InspectAdapter { return w.project(s.State()) })
}
func protocolPath(args []string) []Segment {
	out := []Segment{}
	for _, arg := range args {
		kind, value, _ := strings.Cut(arg, ":")
		s := Segment{Kind: kind, Name: value}
		if kind == "index" {
			s.Index, _ = strconv.ParseUint(value, 10, 64)
		}
		if strings.HasPrefix(kind, "key.") {
			s.Kind = "map_key"
			s.KeyType = kind[4:]
			s.Decimal = value
		}
		out = append(out, s)
	}
	return out
}
func (p *ProtocolSession[S, I, O]) watchCommand(r ProtocolRequest, out ProtocolResponse) ProtocolResponse {
	w := p.watches
	registry := w.registry
	field := func(k, v string) { out.Fields = append(out.Fields, ProtocolField{k, v}) }
	flag := func(k string, v bool) { field(k, "bool:"+strconv.FormatBool(v)) }
	fail := func(code string) ProtocolResponse { out.Fields = nil; out.Error = code; return out }
	reserve := func() bool {
		q := out
		q.Revision = math.MaxUint64
		q.Configuration = math.MaxUint64
		_, err := EncodeResponse(q, p.limits)
		return err == nil
	}
	id := uint64(0)
	if len(r.Args) > 0 {
		id, _ = parseNumber(r.Args[0])
	}
	switch r.Command {
	case "watch":
		config := ChangedWatch(protocolPath(r.Args[2:]))
		config.Selector.SchemaVersion = uint32(id)
		config.Limits.MaxBytes = min(registry.limits.MaxEventBytes, uint64(p.limits.MaxFrame/8))
		config.Limits.MaxValueBytes = uint64(p.limits.MaxField / 4)
		config.Limits.MaxNodes = 128
		config.Limits.MaxDepth = 16
		config.Limits.MaxPageSize = uint64(p.limits.MaxPage)
		config.Limit = uint64(p.limits.MaxPage)
		field("watch.id", number(math.MaxUint64))
		field("watch.generation", number(math.MaxUint64))
		field("watch.effective_sequence", number(math.MaxUint64))
		field("watch.installed_at_revision", number(r.Revision))
		field("watch.baseline", "enum:pending_at_effective_boundary")
		flag("watch.metadata_complete", false)
		if !reserve() {
			return fail("limit")
		}
		ack, err := func() (ResolvedWatchAck, error) {
			old := p.session.busy
			p.session.busy = true
			defer func() { p.session.busy = old }()
			return registry.AddValidated(registry.CaptureRevision(), config, w.project(p.session.State()), SnapshotID{p.session.Epoch(), p.session.Revision(), p.session.Sequence()})
		}()
		if err != nil {
			return fail(p.classifyWatchError(err))
		}
		p.configuration = ack.CaptureRevision
		out.Fields[0].Value = number(ack.WatchID)
		out.Fields[1].Value = number(ack.Generation)
		out.Fields[2].Value = number(ack.EffectiveSequence)
		base := append([]ProtocolField{}, out.Fields...)
		field("watch.display_schema_version", number(uint64(ack.DisplaySchema.Version)))
		field("watch.display_schema", "string:"+ack.DisplaySchema.Name)
		field("watch.resolved_type", "enum:"+ack.ResolvedKind)
		if reserve() {
			out.Fields[5].Value = "bool:true"
		} else {
			out.Fields = base
		}
	case "unwatch":
		entry := registry.entries[id]
		if entry == nil {
			return fail("stale_handle")
		}
		generation := entry.generation + 1
		field("watch.id", number(id))
		field("watch.generation", number(generation))
		field("watch.effective_sequence", number(registry.boundary()))
		if !reserve() {
			return fail("limit")
		}
		if err := registry.Remove(id); err != nil {
			return fail(p.classifyWatchError(err))
		}
		p.configuration = registry.CaptureRevision()
	case "watch_status":
		ids := []uint64{}
		for id := range registry.entries {
			ids = append(ids, id)
		}
		sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
		field("watches.total", number(uint64(len(ids))))
		flag("watches.truncated", len(ids) > p.limits.MaxPage)
		for i, id := range ids[:min(len(ids), p.limits.MaxPage)] {
			entry := registry.entries[id]
			prefix := "watches." + strconv.Itoa(i)
			field(prefix+".id", number(id))
			field(prefix+".generation", number(entry.generation))
			flag(prefix+".enabled", entry.config.Enabled)
			flag(prefix+".has_baseline", entry.baseline != nil)
			field(prefix+".effective_sequence", number(entry.effectiveSequence))
		}
		field("watches.dropped_events", number(registry.droppedTotal))
		field("watches.observation_gaps", number(registry.observationGaps))
		field("watches.inspection_errors", number(registry.inspectionFailures))
	case "watch_current":
		entry := registry.entries[id]
		if entry == nil || entry.current == nil {
			field("watch.value", "enum:unavailable")
			break
		}
		event := entry.current
		field("watch.id", number(id))
		field("watch.generation", number(event.Generation))
		field("watch.capture_revision", number(event.CaptureRevision))
		field("watch.snapshot_revision", number(event.Snapshot.Revision))
		field("watch.snapshot_sequence", number(event.Snapshot.Sequence))
		kind := event.Kind
		if strings.HasPrefix(kind, "baseline_") {
			kind = "Baseline(" + protocolVariant(kind[9:]) + ")"
		} else if kind == "inspection_error" {
			kind = "InspectorError(" + event.InspectionError + ")"
		} else if kind == "sampled_change" {
			kind = "ChangeSinceLastSample { changed: " + strconv.FormatBool(event.Diff == DiffChanged) + " }"
		} else if kind == "watch_value" {
			kind = protocolVariant(string(event.Diff))
		} else {
			kind = protocolVariant(kind)
		}
		field("watch.kind", "enum:"+kind)
		completeness := "Complete"
		if event.Completeness != "" {
			completeness = "Partial(" + protocolVariant(event.Completeness) + ")"
		}
		field("watch.completeness", "enum:"+completeness)
		field("watch.origin", "enum:"+protocolVariant(event.Origin))
		if event.Node != nil {
			out.Fields = append(out.Fields, protocolNode("watch.node", *event.Node)...)
		}

	}
	return out
}
func watchProtocolError(err error) string {
	m := err.Error()
	for _, p := range [][2]string{{"unauthorized", "unauthorized"}, {"redacted", "unauthorized"}, {"unknown watch", "stale_handle"}, {"exhausted", "exhausted"}, {"capacity", "limit"}, {"budget", "limit"}, {"callback active", "busy"}} {
		if strings.Contains(m, p[0]) {
			return p[1]
		}
	}
	return "invalid_input"
}
func protocolNode(prefix string, node InspectNode) []ProtocolField {
	fields := []ProtocolField{}
	put := func(k, v string) { fields = append(fields, ProtocolField{prefix + "." + k, v}) }
	completeness := "Complete"
	if node.Incomplete != "" {
		completeness = "Partial(" + protocolVariant(node.Incomplete) + ")"
	}
	put("completeness", "enum:"+completeness)
	count := "none"
	if node.ChildCount != nil {
		count = number(*node.ChildCount)
	}
	put("child_count", count)
	kind, value := node.Kind, "none"
	if node.Scalar != nil {
		v := node.Scalar
		switch v.Kind {
		case "bool":
			kind = "bool"
			value = "bool:" + strconv.FormatBool(v.Bool)
		case "unit":
			kind = "unit"
			value = "unit"
		case "char":
			kind = "char"
			value = "char:" + v.Text
		case "f32", "f64":
			kind = "float"
			value = v.Kind + ":" + v.Decimal
			put("bits", number(v.Bits))
		default:
			kind = "integer"
			value = v.Kind + ":" + v.Decimal
		}
	}
	switch kind {
	case "object", "sequence":
		value = "string:" + node.Name
	case "opaque":
		value = "string:" + node.Name
	case "string":
		value = "string:" + node.Text
		put("total_bytes", number(node.TotalBytes))
	case "bytes":
		value = "bytes:" + hex.EncodeToString(node.Bytes)
		put("total_bytes", number(node.TotalBytes))
	case "enum":
		value = "string:" + node.Name
		put("variant_id", "string:"+node.VariantID)
		put("variant_label", "string:"+node.VariantLabel)
	case "map":
		value = "enum:" + protocolVariant(node.Ordering)
	case "truncated":
		value = "enum:" + protocolVariant(node.Incomplete)
	}
	put("kind", "enum:"+kind)
	put("value", value)
	put("children.returned", number(uint64(len(node.Children))))
	if node.Page != nil {
		p := node.Page
		put("page.offset", number(p.Offset))
		put("page.returned", number(p.Returned))
		put("page.total", number(p.Total))
		next := "none"
		if p.NextOffset != nil {
			next = number(*p.NextOffset)
		}
		put("page.next", next)
	}
	for i, c := range node.Children {
		key := prefix + ".children." + strconv.Itoa(i)
		seg := c.Segment.Kind + ":" + c.Segment.Name
		if c.Segment.Kind == "index" {
			seg = "index:" + strconv.FormatUint(c.Segment.Index, 10)
		}
		if c.Segment.Kind == "map_key" {
			v := c.Segment.Decimal
			if c.Segment.KeyType == "string" || c.Segment.KeyType == "char" {
				v = c.Segment.Name
			}
			seg = "key." + c.Segment.KeyType + ":" + v
		}
		fields = append(fields, ProtocolField{key + ".label", "string:" + c.Label}, ProtocolField{key + ".segment", seg})
		fields = append(fields, protocolNode(key+".node", c.Node)...)
	}
	return fields
}

func protocolVariant(value string) string {
	parts := strings.Split(value, "_")
	for i, p := range parts {
		if len(p) > 0 {
			parts[i] = strings.ToUpper(p[:1]) + p[1:]
		}
	}
	return strings.Join(parts, "")
}

// Exception formatting is cooperative host code too. Keep the session borrowed
// while mapping service failures so Error cannot deliver an input or cancel.
func (p *ProtocolSession[S, I, O]) classifyWatchError(err error) string {
	old := p.session.busy
	p.session.busy = true
	defer func() { p.session.busy = old }()
	return watchProtocolError(err)
}
