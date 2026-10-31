package debug

import (
	"errors"
	"math"
	"strings"
	"unicode/utf8"
)

// DiagnosticMarker is a post-turn classification; it must not affect application behavior.
type DiagnosticMarker struct {
	ID     string
	Detail string
}

// MarkerBatch reports retained markers and logical byte accounting.
type MarkerBatch struct {
	Markers       []DiagnosticMarker
	Dropped       uint64
	Truncated     uint64
	RetainedBytes int
}

// MarkerCollector retains bounded post-turn markers. It is single-threaded and
// detail callbacks must not reenter it. The byte budget uses a 48-byte logical
// Rust 64-bit marker overhead; it does not bound Go heap or callback allocation.
type MarkerCollector struct {
	enabled              bool
	maxRecords, maxBytes int
	batch                MarkerBatch
	busy, finished       bool
}

const markerOverhead = 48

var errMarkerState = errors.New("marker collector is finished or reentered")
var errMarkerText = errors.New("marker text must be valid UTF-8")

// NewMarkerCollector creates a collector with nonnegative logical limits.
func NewMarkerCollector(enabled bool, maxRecords, maxBytes int) (*MarkerCollector, error) {
	if maxRecords < 0 || maxBytes < 0 {
		return nil, errors.New("invalid marker limits")
	}
	return &MarkerCollector{enabled: enabled, maxRecords: maxRecords, maxBytes: maxBytes}, nil
}

// DisabledMarkerCollector returns the disabled Rust-profile default.
func DisabledMarkerCollector() *MarkerCollector {
	c, _ := NewMarkerCollector(false, 256, 32*1024)
	return c
}

// Interested indicates whether a marker could fit before accounting for its ID.
func (c *MarkerCollector) Interested() bool {
	return c.enabled && !c.busy && !c.finished && len(c.batch.Markers) < c.maxRecords && c.batch.RetainedBytes < c.maxBytes
}

func markerIncrement(n *uint64) {
	if *n < math.MaxUint64 {
		*n++
	}
}

// Marker calls detail only after ID and overhead admission. Returned text must be
// valid UTF-8. Oversized detail is truncated at a Unicode boundary.
func (c *MarkerCollector) Marker(id string, detail func() string) error {
	if c.busy || c.finished {
		return errMarkerState
	}
	if !c.enabled {
		return nil
	}
	remaining := c.maxBytes - c.batch.RetainedBytes
	if !c.Interested() || markerOverhead > remaining || len(id) > remaining-markerOverhead {
		markerIncrement(&c.batch.Dropped)
		return nil
	}
	if detail == nil || !utf8.ValidString(id) {
		return errMarkerText
	}
	c.busy = true
	var value string
	func() { defer func() { c.busy = false }(); value = detail() }()
	if !utf8.ValidString(value) {
		return errMarkerText
	}
	available := remaining - markerOverhead - len(id)
	if len(value) > available {
		end := available
		for end > 0 && !utf8.RuneStart(value[end]) {
			end--
		}
		value = value[:end]
		markerIncrement(&c.batch.Truncated)
	}
	c.batch.RetainedBytes += markerOverhead + len(id) + len(value)
	c.batch.Markers = append(c.batch.Markers, DiagnosticMarker{ID: strings.Clone(id), Detail: strings.Clone(value)})
	return nil
}

// Finish ends collection and returns a detached batch. It fails during a callback
// and on repeated calls.
func (c *MarkerCollector) Finish() (MarkerBatch, error) {
	if c.busy || c.finished {
		return MarkerBatch{}, errMarkerState
	}
	c.finished = true
	result := c.batch
	result.Markers = append([]DiagnosticMarker(nil), c.batch.Markers...)
	return result, nil
}
