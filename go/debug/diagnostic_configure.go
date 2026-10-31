package debug

import (
	"fmt"
	"math"
	"sort"
)

// CaptureConfigurationAck reports the new revision and the producers that must
// separately acknowledge it at their own ordered boundaries.
type CaptureConfigurationAck struct {
	CaptureRevision  uint64
	PendingProducers []uint64
}

// ConfigureSubscriptions replaces the selected subscriptions as one revision.
// Validation is performed against isolated queues before any live state changes.
// Older subscriptions remain available to producers that have not crossed their
// acknowledged boundary, subject to a bounded retained-generation budget.
func (d *Diagnostics) ConfigureSubscriptions(expected uint64, configs []ProbeSubscription) (CaptureConfigurationAck, error) {
	if d.busy {
		return CaptureConfigurationAck{}, fmt.Errorf("capture active")
	}
	if expected != d.revision {
		return CaptureConfigurationAck{}, fmt.Errorf("stale capture revision")
	}
	if len(configs) > d.limits.MaxSubscriptions {
		return CaptureConfigurationAck{}, fmt.Errorf("subscription capacity")
	}
	if d.revision == math.MaxUint64 || uint64(len(configs)) > math.MaxUint64-d.nextSubscription {
		return CaptureConfigurationAck{}, fmt.Errorf("revision exhausted")
	}

	// Use the same admission logic as Subscribe without touching live rings,
	// counters, producer state, or callback-owned input.
	scratch, err := NewDiagnostics(d.limits)
	if err != nil {
		return CaptureConfigurationAck{}, err
	}
	for id, sink := range d.sinks {
		q, err := NewWatchRegistry(d.limits.Watch, sink.auth)
		if err != nil {
			return CaptureConfigurationAck{}, err
		}
		scratch.sinks[id] = &diagnosticSink{queue: q, auth: sink.auth}
	}
	staged := make([]*probeEntry, 0, len(configs))
	for _, config := range configs {
		id, err := scratch.Subscribe(scratch.revision, config)
		if err != nil {
			return CaptureConfigurationAck{}, err
		}
		staged = append(staged, scratch.subscriptions[id])
	}

	// Retain old generations only where a registered producer can still use
	// them. Do not silently discard a generation on capacity exhaustion.
	nextRevision := d.revision + 1
	retained := make(map[uint64]*probeEntry, len(d.subscriptions)+len(staged))
	retainedStates := 0
	for id, entry := range d.subscriptions {
		retired := entry.removedRevision
		if retired == 0 {
			retired = nextRevision
		}
		keep := false
		for _, producer := range d.producers {
			if producer.active >= entry.addedRevision && producer.active < retired {
				keep = true
				break
			}
		}
		if keep {
			clone := *entry
			clone.removedRevision = retired
			retained[id] = &clone
			retainedStates += len(entry.registries) + len(entry.markerCounts) + len(entry.pendingReasons)
		}
	}
	// Bound unacknowledged configurations even when the host never advances a
	// producer. Limit in terms of the already configured producer budget.
	capacity := math.MaxInt
	if d.limits.MaxProducers < math.MaxInt && d.limits.MaxSubscriptions <= math.MaxInt/(d.limits.MaxProducers+1) {
		capacity = d.limits.MaxSubscriptions * (d.limits.MaxProducers + 1)
	}
	if len(retained) > capacity || len(staged) > capacity-len(retained) {
		return CaptureConfigurationAck{}, fmt.Errorf("retained subscription capacity")
	}

	for i, entry := range staged {
		id := d.nextSubscription + uint64(i)
		entry.addedRevision = nextRevision
		entry.removedRevision = 0
		retained[id] = entry
	}
	pending := make([]uint64, 0, len(d.producers))
	for id := range d.producers {
		pending = append(pending, id)
	}
	sort.Slice(pending, func(i, j int) bool { return pending[i] < pending[j] })
	d.subscriptions = retained
	d.probeStates = retainedStates
	d.nextSubscription += uint64(len(staged))
	d.revision = nextRevision
	return CaptureConfigurationAck{CaptureRevision: nextRevision, PendingProducers: pending}, nil
}
