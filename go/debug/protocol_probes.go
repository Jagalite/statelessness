package debug

import (
	"encoding/json"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ProtocolProbeProfile is an explicitly host-approved diagnostic selection.
type ProtocolProbeProfile struct {
	ID           uint64
	Label        string
	Subscription ProbeSubscription
}
type protocolProbeProfiles struct {
	hub              *Diagnostics
	profiles         []ProtocolProbeProfile
	sinks            []uint64
	selected         []uint64
	selectedRevision uint64
	known            bool
}

// AttachProbeProfiles attaches owned profiles and a bounded independent consumer
// for each exposed sink. Consumers receive supported records published after
// attachment; existing host records are not replayed. At most one protocol
// consumer may attach to each sink. DetachProbeProfiles releases its queues.
func (p *ProtocolSession[S, I, O]) AttachProbeProfiles(hub *Diagnostics, profiles []ProtocolProbeProfile, sinks []uint64) error {
	if p.busy || (hub != nil && hub.busy) {
		return ProtocolError("busy")
	}
	if p.probes != nil || hub == nil {
		return ProtocolError("invalid_input")
	}
	if len(profiles) > 64 || len(sinks) > 64 || hub.limits.MaxProducers > 64 || hub.limits.MaxSubscriptions > 64 {
		return ProtocolError("limit")
	}
	seen := map[uint64]bool{}
	copies := make([]ProtocolProbeProfile, len(profiles))
	cost := 0
	for i, v := range profiles {
		if v.ID == 0 || seen[v.ID] || !utf8.ValidString(v.Label) || len(v.Label) > min(128, p.limits.MaxField-7) {
			return ProtocolError("invalid_input")
		}
		seen[v.ID] = true
		config := v.Subscription
		// Bound host-owned data before cloning or JSON encoding it. In
		// particular, do not copy an unbounded path or selector first.
		if len(config.Watch.Selector.Path) > 32 {
			return ProtocolError("limit")
		}
		remaining := p.limits.MaxCandidateBytes - cost - len(v.Label)
		charge := func(value string) bool {
			if len(value) > remaining {
				return false
			}
			remaining -= len(value)
			return true
		}
		values := []string{config.Site, config.Scope.Origin, config.Scope.Model, config.Scope.Machine, config.Scope.Entity, config.Scope.Correlation, config.Scope.InputVariant, config.Scope.OutputVariant, config.Scope.Property, config.Scope.EffectKind, config.Scope.SourceSite, config.Watch.Selector.Model, config.Watch.Selector.Machine, config.Watch.Selector.InputVariant, config.Watch.Selector.OutputVariant, config.Watch.Selector.Origin, config.Watch.Trigger}
		if remaining < 0 {
			return ProtocolError("limit")
		}
		for _, value := range values {
			if !charge(value) {
				return ProtocolError("limit")
			}
		}
		for _, segment := range config.Watch.Selector.Path {
			for _, value := range []string{segment.Kind, segment.Name, segment.KeyType, segment.Decimal} {
				if !charge(value) {
					return ProtocolError("limit")
				}
			}
		}
		if config.Watch.Equals != nil {
			for _, value := range []string{config.Watch.Equals.Kind, config.Watch.Equals.Decimal, config.Watch.Equals.Text} {
				if !charge(value) {
					return ProtocolError("limit")
				}
			}
		}
		config.Site = strings.Clone(config.Site)
		config.Scope = cloneScope(config.Scope)
		config.Watch = cloneWatchConfig(config.Watch)
		encoded, err := json.Marshal(config)
		if err != nil || len(encoded) > p.limits.MaxCandidateBytes-cost-len(v.Label) {
			return ProtocolError("limit")
		}
		cost += len(encoded) + len(v.Label)
		copies[i] = ProtocolProbeProfile{v.ID, strings.Clone(v.Label), config}
	}
	seen = map[uint64]bool{}
	ownedSinks := append([]uint64(nil), sinks...)
	for _, id := range ownedSinks {
		if seen[id] || hub.sinks[id] == nil || hub.sinks[id].protocolQueue != nil {
			return ProtocolError("invalid_input")
		}
		seen[id] = true
	}
	queues := make([]*WatchRegistry, len(ownedSinks))
	for i, id := range ownedSinks {
		queue, err := NewWatchRegistry(hub.limits.Watch, hub.sinks[id].auth)
		if err != nil {
			return ProtocolError("limit")
		}
		queues[i] = queue
	}
	for i, id := range ownedSinks {
		// A fresh consumer starts with fresh queue counters, like its queue.
		sink := hub.sinks[id]
		sink.protocolQueue, sink.protocolCaptured, sink.protocolAbandoned = queues[i], 0, 0
	}
	p.probes = &protocolProbeProfiles{hub: hub, profiles: copies, sinks: ownedSinks}
	return nil
}
func (p *ProtocolSession[S, I, O]) probeProfileCommand(r ProtocolRequest, out ProtocolResponse) ProtocolResponse {
	attachment := p.probes
	hub := attachment.hub
	fail := func(code string) ProtocolResponse { out.Error = code; out.Fields = nil; return out }
	put := func(key, value string) { out.Fields = append(out.Fields, ProtocolField{key, value}) }
	flag := func(key string, value bool) { put(key, "bool:"+strconv.FormatBool(value)) }
	if hub.busy {
		return fail("busy")
	}
	if r.Command == "probe_events" {
		sink, _ := parseNumber(r.Args[0])
		limit, _ := parseNumber(r.Args[1])
		if limit == 0 || limit > uint64(p.limits.MaxPage) {
			return fail("limit")
		}
		allowed := false
		for _, id := range attachment.sinks {
			if id == sink {
				allowed = true
				break
			}
		}
		if !allowed {
			return fail("unauthorized")
		}
		queue := hub.sinks[sink].protocolQueue
		events, err := previewDiagnosticQueue(queue, int(limit))
		if err != nil {
			return fail("busy")
		}
		health := hub.sinks[sink].protocolHealth()
		put("probes.sink", number(sink))
		put("probes.capture_revision", number(hub.revision))
		put("probes.sink.health.dropped", number(health.Dropped))
		put("probes.sink.health.exporter_failed", number(health.ExporterFailed))
		origins := map[string]string{"simulation": "Simulation", "live": "Live", "debug_controlled_live": "DebugControlledLive", "test": "Test", "replay": "Replay", "imported": "Imported"}
		severities := []string{"Trace", "Debug", "Info", "Warn", "Error"}
		for i, event := range events {
			m := event.Diagnostic
			// enqueueDiagnostic admits only fully enveloped records; keep the
			// check so a violated invariant fails closed instead of inventing data.
			if !protocolEnveloped(event) {
				return fail("unsupported")
			}
			origin, ok := origins[m.Origin]
			if !ok || int(m.Severity) >= len(severities) {
				return fail("invalid_input")
			}
			key := "probes.events." + strconv.Itoa(i)
			for _, pair := range []struct {
				key   string
				value uint64
			}{{"schema_version", 1}, {"run", m.RunID}, {"producer", m.Producer}, {"epoch", m.Epoch}, {"producer_sequence", m.ProducerSequence}, {"capture_revision", event.CaptureRevision}, {"transition", m.Transition}} {
				put(key+"."+pair.key, number(pair.value))
			}
			if m.Kind != "marker" && m.Kind != "probe" {
				return fail("unsupported")
			}
			reason := "none"
			if m.BaselineReason != "" {
				allowed := map[string]bool{"initial": true, "observation_gap": true, "inspection_incomplete": true, "type_unavailable": true, "selector_gap": true, "reconfigured": true, "schema_changed": true, "source_changed": true, "evicted": true}
				if !allowed[m.BaselineReason] {
					return fail("unsupported")
				}
				reason = "enum:" + protocolVariant(m.BaselineReason)
			}
			put(key+".origin", "enum:"+origin)
			put(key+".severity", "enum:"+severities[m.Severity])
			put(key+".kind", "enum:"+protocolVariant(m.Kind))
			flag(key+".baseline", m.Baseline)
			put(key+".baseline_reason", reason)
			flag(key+".change_unknown", m.ChangeUnknown)
			flag(key+".incomplete_turn", m.IncompleteTurn)
			if r.Args[2] == "bool:false" {
				put(key+".metadata_omitted", "bool:true")
				put(key+".payload", "omitted:by_request")
			} else {
				put(key+".site", "string:"+m.Site)
				for j, segment := range m.Path {
					encoded := segment.Kind + ":" + segment.Name
					if segment.Kind == "index" {
						encoded = "index:" + strconv.FormatUint(segment.Index, 10)
					}
					if segment.Kind == "map_key" {
						value := segment.Decimal
						if segment.KeyType == "string" || segment.KeyType == "char" {
							value = segment.Name
						}
						encoded = "key." + segment.KeyType + ":" + value
					}
					put(key+".path."+strconv.Itoa(j), encoded)
				}
				if m.Kind == "probe" && m.SchemaKnown {
					put(key+".display_schema", "string:"+event.SchemaName)
					put(key+".display_schema_version", number(uint64(m.SchemaVersion)))
				}
				if event.Node != nil {
					fields, err := protocolNode(key+".payload", *event.Node)
					if err != nil {
						return fail("limit")
					}
					out.Fields = append(out.Fields, fields...)
				} else {
					put(key+".payload", "none")
				}
			}
		}
		put("probes.events.returned", number(uint64(len(events))))
		q := out
		q.Revision, q.Configuration = math.MaxUint64, math.MaxUint64
		if _, err = EncodeResponse(q, p.limits); err != nil {
			return fail("limit")
		}
		if err = consumeDiagnosticQueue(queue, len(events)); err != nil {
			return fail("callback")
		}
		return out
	}
	if r.Command == "probe_configure" {
		expected, _ := parseNumber(r.Args[0])
		if expected != hub.revision {
			return fail("stale_configuration")
		}
		if len(r.Args)-1 > 64 {
			return fail("limit")
		}
		configs := make([]ProbeSubscription, 0, len(r.Args)-1)
		ids := make([]uint64, 0, len(r.Args)-1)
		seen := map[uint64]bool{}
		for _, arg := range r.Args[1:] {
			id, _ := parseNumber(arg)
			if seen[id] {
				return fail("invalid_input")
			}
			seen[id] = true
			found := false
			for _, profile := range attachment.profiles {
				if profile.ID == id {
					configs = append(configs, profile.Subscription)
					found = true
					break
				}
			}
			if !found {
				return fail("unauthorized")
			}
			ids = append(ids, id)
		}
		producers := hub.ProducerStatus()
		put("probes.capture_revision", number(math.MaxUint64))
		put("probes.selected_count", number(uint64(len(ids))))
		put("probes.pending_producers.total", number(uint64(len(producers))))
		flag("probes.pending_producers.truncated", len(producers) > p.limits.MaxPage)
		reserve := func() bool {
			q := out
			q.Revision, q.Configuration = math.MaxUint64, math.MaxUint64
			_, err := EncodeResponse(q, p.limits)
			return err == nil
		}
		if !reserve() {
			return fail("limit")
		}
		for i, v := range producers[:min(len(producers), p.limits.MaxPage)] {
			put("probes.pending_producers."+strconv.Itoa(i), number(v.Producer))
			if !reserve() {
				out.Fields = out.Fields[:len(out.Fields)-1]
				out.Fields[3].Value = "bool:true"
				break
			}
		}
		ack, err := hub.ConfigureSubscriptions(expected, configs)
		if err != nil {
			return fail(watchProtocolError(err))
		}
		out.Fields[0].Value = number(ack.CaptureRevision)
		attachment.selected = ids
		attachment.selectedRevision = ack.CaptureRevision
		attachment.known = true
		return out
	}
	offset, _ := parseNumber(r.Args[0])
	limit, _ := parseNumber(r.Args[1])
	if limit == 0 || limit > uint64(p.limits.MaxPage) || offset > math.MaxUint64-limit {
		return fail("limit")
	}
	producers := hub.ProducerStatus()
	pending := 0
	for _, v := range producers {
		if v.CaptureRevision != hub.revision {
			pending++
		}
	}
	put("probes.capture_revision", number(hub.revision))
	put("probes.producers.total", number(uint64(len(producers))))
	flag("probes.producers.truncated", offset+limit < uint64(len(producers)))
	put("probes.pending_producers.total", number(uint64(pending)))
	for i := offset; i < min(offset+limit, uint64(len(producers))); i++ {
		v := producers[i]
		key := "probes.producers." + strconv.FormatUint(i-offset, 10)
		put(key+".id", number(v.Producer))
		put(key+".epoch", number(v.Epoch))
		put(key+".capture_revision", number(v.CaptureRevision))
		put(key+".effective_transition", number(v.EffectiveSequence))
		flag(key+".pending", v.CaptureRevision != hub.revision)
	}
	total := max(len(producers), len(attachment.profiles), len(attachment.sinks))
	put("probes.status_live", "bool:true")
	put("probes.offset", number(offset))
	if offset+limit < uint64(total) {
		put("probes.next_offset", number(offset+limit))
	} else {
		put("probes.next_offset", "none")
	}
	put("probes.profiles.total", number(uint64(len(attachment.profiles))))
	flag("probes.profiles.truncated", offset+limit < uint64(len(attachment.profiles)))
	for i := offset; i < min(offset+limit, uint64(len(attachment.profiles))); i++ {
		v := attachment.profiles[i]
		key := "probes.profiles." + strconv.FormatUint(i-offset, 10)
		put(key+".id", number(v.ID))
		put(key+".label", "string:"+v.Label)
	}
	known := attachment.known && attachment.selectedRevision == hub.revision
	flag("probes.selected_profiles_known", known)
	if known {
		for i := offset; i < min(offset+limit, uint64(len(attachment.selected))); i++ {
			put("probes.selected."+strconv.FormatUint(i-offset, 10), number(attachment.selected[i]))
		}
	}
	put("probes.sinks.total", number(uint64(len(attachment.sinks))))
	for i := offset; i < min(offset+limit, uint64(len(attachment.sinks))); i++ {
		id := attachment.sinks[i]
		key := "probes.sinks." + strconv.FormatUint(i-offset, 10)
		// Report the queue that probe_events drains (Rust has exactly one).
		sink := hub.sinks[id]
		q := sink.protocolQueue
		if q == nil {
			q = sink.queue
		}
		put(key+".id", number(id))
		put(key+".queued_records", number(uint64(len(q.ring))))
		put(key+".queued_bytes", number(q.ringBytes))
		appendProbeHealth(&out.Fields, key, sink.protocolHealth())
	}
	return out
}

// appendProbeHealth mirrors Rust protocol.rs append_probe_health: all ten
// SinkHealth counters in the reference order.
func appendProbeHealth(fields *[]ProtocolField, prefix string, h SinkHealth) {
	for _, pair := range []struct {
		key   string
		value uint64
	}{{"captured", h.Captured}, {"filtered", h.Filtered}, {"sampled", h.Sampled}, {"dropped", h.Dropped}, {"truncated", h.Truncated}, {"exporter_failed", h.ExporterFailed}, {"abandoned", h.Abandoned}, {"inspection_failed", h.InspectionFailed}, {"baseline_evicted", h.BaselineEvicted}, {"observation_gaps", h.ObservationGaps}} {
		*fields = append(*fields, ProtocolField{prefix + ".health." + pair.key, number(pair.value)})
	}
}

// DetachProbeProfiles releases the independent protocol queues. Host sink
// queues and subscriptions remain owned by the diagnostic hub.
func (p *ProtocolSession[S, I, O]) DetachProbeProfiles() error {
	if p.busy || (p.probes != nil && p.probes.hub.busy) {
		return ProtocolError("busy")
	}
	if p.probes == nil {
		return ProtocolError("invalid_input")
	}
	for _, id := range p.probes.sinks {
		p.probes.hub.sinks[id].protocolQueue = nil
	}
	p.probes = nil
	return nil
}
