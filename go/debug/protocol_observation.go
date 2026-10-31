package debug

import (
	"encoding/hex"
	"strconv"

	s "github.com/Jagalite/statelessness/go"
)

func (p *ProtocolSession[S, I, O]) status(out ProtocolResponse) ProtocolResponse {
	field := func(key, value string) { out.Fields = append(out.Fields, ProtocolField{key, value}) }
	flag := func(key string, value bool) { field(key, "bool:"+strconv.FormatBool(value)) }
	phase := map[Phase]string{Ready: "Ready", Paused: "Paused", Ended: "Ended"}[p.session.Phase()]
	field("mode", "enum:simulation")
	field("phase", "enum:"+phase)
	field("sequence", number(p.session.Sequence()))
	field("environment", "string:"+p.session.InputPolicy())
	flag("capability.input_delivery", p.authority.DeliverInputs)
	flag("capability.inspection", p.adapters["inspect"] != nil)
	flag("capability.watch_configuration", p.authority.ConfigureWatches && (p.watches != nil || p.adapters["watch_status"] != nil))
	flag("capability.exact_values", p.authority.ReadExactValues)
	flag("capability.live_control", false)
	flag("capability.live_restore", false)
	flag("capability.effects", false)
	flag("capability.probe_queries", p.probes != nil || p.adapters["probe_status"] != nil)
	flag("capability.probe_configuration", p.authority.ConfigureDiagnostics && (p.probes != nil || p.adapters["probe_status"] != nil))
	flag("capability.trace_export", p.authority.ExportExactTrace && p.session.recorder != nil)
	flag("capability.effect_telemetry", p.adapters["telemetry_health"] != nil)
	flag("capability.metric_queries", p.adapters["metric_snapshot"] != nil)
	field("limits.telemetry_snapshot_bytes", number(uint64(p.limits.MaxTelemetrySnapshot)))
	field("limits.frame_bytes", number(uint64(p.limits.MaxFrame)))
	field("limits.dedup_entries", number(uint64(p.limits.MaxCache)))
	field("limits.dedup_bytes", number(uint64(p.limits.MaxCacheBytes)))
	field("limits.page_size", number(uint64(p.limits.MaxPage)))
	field("events.dropped", number(p.eventDrops))
	stop := "none"
	if reason := p.session.Stop(); reason != nil {
		stop = reason.Kind
	}
	field("stop", "enum:"+stop)
	return out
}

// These projections are native debugger operations. A pipe host supplies model
// callbacks, never reconstructed wire fields for the session's observations.
func (p *ProtocolSession[S, I, O]) observationFields(command string, out ProtocolResponse) ProtocolResponse {
	field := func(key, value string) { out.Fields = append(out.Fields, ProtocolField{key, value}) }
	flag := func(key string, value bool) { field(key, "bool:"+strconv.FormatBool(value)) }
	last := p.session.last
	if command == "checks" {
		checks := p.session.initial
		if last != nil {
			checks = last.Checks
		}
		flag("checks.complete", p.session.ChecksComplete())
		field("checks.total", number(uint64(len(checks))))
		failed := false
		for index, check := range checks {
			if check.Status != "failed" {
				continue
			}
			failed = true
			phase := "initial_state"
			if last != nil {
				phase = "transition"
				if index < last.StateCheckCount {
					phase = "post_state"
				}
			}
			field("checks.first_failure.id", "string:"+check.ID)
			field("checks.first_failure.index", number(uint64(index)))
			field("checks.first_failure.phase", "enum:"+phase)
			field("checks.first_failure.sequence", number(p.session.Sequence()))
			break
		}
		if !failed {
			field("checks.first_failure", "none")
		}
		count := min(len(checks), p.limits.MaxPage)
		field("checks.returned", number(uint64(count)))
		flag("checks.truncated", count < len(checks))
		for index, check := range checks[:count] {
			prefix := "checks." + strconv.Itoa(index)
			field(prefix+".id", "string:"+check.ID)
			field(prefix+".status", "enum:"+check.Status)
		}
		return out
	}
	if last == nil {
		field("outputs.total", number(0))
		return out
	}
	field("outputs.total", number(uint64(len(last.Transition.Outputs))))
	field("disposition", "enum:"+last.Transition.Disposition.Kind)
	field("outputs.provenance", "enum:simulated")
	if !p.authority.ReadExactValues {
		field("outputs.payloads", "enum:unavailable")
		return out
	}
	field("outputs.payloads", "enum:exact_sensitive")
	oldBusy := p.session.busy
	p.session.busy = true
	defer func() { p.session.busy = oldBusy }()
	count := min(len(last.Transition.Outputs), p.limits.MaxPage)
	for index, output := range last.Transition.Outputs[:count] {
		var bytes []byte
		err := s.WithEncodeBuffer(&bytes, uint64(p.limits.MaxInput), func(buffer *s.EncodeBuffer) error {
			return p.session.model.AppendOutputEncoding(output, buffer)
		})
		if err != nil {
			out.Error = "callback"
			out.Fields = nil
			return out
		}
		field("outputs."+strconv.Itoa(index)+".encoded", "bytes:"+hex.EncodeToString(bytes))
	}
	flag("outputs.truncated", len(last.Transition.Outputs) > p.limits.MaxPage)
	return out
}
