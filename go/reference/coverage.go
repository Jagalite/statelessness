package reference

import (
	"fmt"
	"strconv"
	"strings"

	s "github.com/Jagalite/statelessness/go"
)

func fingerprint(data []byte) string {
	h := uint64(0xcbf29ce484222325)
	for _, b := range data {
		h = (h ^ uint64(b)) * 0x100000001b3
	}
	return fmt.Sprintf("fnv1a64:%016x", h)
}

// Manifest uses an independent requirement file, not the model's emitted checks.
func (c Controller) Manifest() (s.AssuranceManifest, error) {
	if err := c.Bounds.Validate(); err != nil {
		return s.AssuranceManifest{}, err
	}
	propertyBytes, err := sourceFiles.ReadFile("controller-properties.txt")
	if err != nil {
		return s.AssuranceManifest{}, err
	}
	manifestBytes, err := sourceFiles.ReadFile("controller-requirements.json")
	if err != nil {
		return s.AssuranceManifest{}, err
	}
	m := s.AssuranceManifest{Version: 1, Model: "reference-controller", RequiredOutcome: "graph_exhausted", Bounds: map[string]string{"max_generations": strconv.FormatUint(c.Bounds.Generations, 10), "max_attempts": strconv.FormatUint(uint64(c.Bounds.Attempts), 10), "max_time": strconv.FormatUint(c.Bounds.Time, 10), "payload": "outcome abstraction; no real file/network effects during verification", "delivery": "unknown/stale/duplicate deliveries included; eventual delivery not proven"}, Provenance: s.AssuranceProvenance{Manifest: fingerprint(manifestBytes), Model: controllerBuild(c), Properties: fingerprint(propertyBytes)}}
	for _, line := range strings.Split(strings.TrimSuffix(string(propertyBytes), "\n"), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			return s.AssuranceManifest{}, &s.ModelError{Message: "invalid independent property declaration"}
		}
		m.Properties = append(m.Properties, s.AssuranceProperty{ID: fields[1], Phase: fields[0]})
	}
	for _, name := range []string{"submit", "cancel", "complete", "cancelled", "tick"} {
		m.Families = append(m.Families, s.AssuranceFamily{Name: name})
	}
	for _, id := range []string{"supersede_running", "stale_success", "cancel_ack", "retry_started", "duplicate_completion", "settled_cleanup"} {
		m.Goals = append(m.Goals, s.AssuranceGoal{ID: id, Scope: "transition"})
	}
	if err = m.Validate(); err != nil {
		return s.AssuranceManifest{}, err
	}
	return m, nil
}
func Coverage(before State, in Input, after s.Transition[State, Effect], _ []Input) (s.AssuranceHits, error) {
	if err := validateObservation(before, in, after); err != nil {
		return s.AssuranceHits{}, err
	}
	switch in.Kind {
	case "submit", "cancel", "complete", "cancelled", "tick":
	default:
		return s.AssuranceHits{}, &s.ModelError{Message: "invalid controller input family"}
	}
	hits := s.AssuranceHits{Families: []string{in.Kind}}
	if in.Kind == "submit" && before.Current != nil {
		j, ok := before.Job(*before.Current)
		if ok && j.Phase == "running" && len(after.State.Jobs) > len(before.Jobs) {
			hits.Goals = append(hits.Goals, "supersede_running")
		}
	}
	if in.Kind == "complete" && in.Outcome == "success" && !current(before, in.Generation) {
		j, ok := before.Job(in.Generation)
		if ok && j.Phase == "cancelling" {
			hits.Goals = append(hits.Goals, "stale_success")
		}
	}
	if in.Kind == "cancelled" {
		j, ok := before.Job(in.Generation)
		if ok && j.Phase == "cancelling" && j.Attempt == in.Attempt {
			hits.Goals = append(hits.Goals, "cancel_ack")
		}
	}
	if in.Kind == "tick" {
		for _, e := range after.Outputs {
			if e.Kind == "start" && e.Attempt > 1 {
				hits.Goals = append(hits.Goals, "retry_started")
				break
			}
		}
	}
	if in.Kind == "complete" {
		j, ok := before.Job(in.Generation)
		if ok && j.Phase == "settled" {
			hits.Goals = append(hits.Goals, "duplicate_completion")
		}
	}
	for _, e := range after.Outputs {
		if e.Kind == "release" {
			hits.Goals = append(hits.Goals, "settled_cleanup")
			break
		}
	}
	return hits, nil
}
