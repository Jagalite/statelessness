package debug

import "fmt"

// previewSink makes detached, bounded copies without consuming the queue, so a
// protocol response can be fully encoded before it commits a destructive read.
func (d *Diagnostics) previewSink(id uint64, limit int) ([]WatchEvent, error) {
	if d.busy {
		return nil, fmt.Errorf("capture active")
	}
	sink := d.sinks[id]
	if sink == nil {
		return nil, fmt.Errorf("unknown sink")
	}
	if limit < 0 {
		return nil, fmt.Errorf("invalid event limit")
	}
	return previewDiagnosticQueue(sink.queue, limit)
}

func previewDiagnosticQueue(q *WatchRegistry, limit int) ([]WatchEvent, error) {
	count := min(limit, len(q.ring))
	events := make([]WatchEvent, 0, count)
	for _, source := range q.ring[:count] {
		// Share the queue's admission copy so previews cannot diverge from it.
		event, ok := q.ownedEvent(source)
		if !ok {
			return nil, fmt.Errorf("queued event projection exceeds its admission bounds")
		}
		events = append(events, event)
	}
	return events, nil
}

// consumeSink is called only after response preflight under the same single-owner
// session. It does not consume the queue's loss counter, which remains health data.
func (d *Diagnostics) consumeSink(id uint64, count int) error {
	if d.busy {
		return fmt.Errorf("capture active")
	}
	sink := d.sinks[id]
	if sink == nil {
		return fmt.Errorf("unknown sink")
	}
	return consumeDiagnosticQueue(sink.queue, count)
}

func consumeDiagnosticQueue(q *WatchRegistry, count int) error {
	if count < 0 || count > len(q.ring) {
		return fmt.Errorf("invalid event count")
	}
	for i := 0; i < count; i++ {
		q.ringBytes -= retainedEvent(q.ring[i])
		q.ring[i] = WatchEvent{}
	}
	q.ring = q.ring[count:]
	if len(q.ring) == 0 {
		q.ring = nil
	}
	return nil
}
