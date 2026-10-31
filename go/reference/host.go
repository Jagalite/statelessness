package reference

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"sync"
	"sync/atomic"
	"time"
)

type workerID struct {
	generation uint64
	attempt    uint8
}
type hostWorker struct {
	cancelled atomic.Bool
	done      chan struct{}
}

// Host has one caller-owned reducer and an ordered effect executor. Worker
// callbacks must return eventually; cancellation is acknowledged only then.
type Host struct {
	model           Controller
	state           State
	work            func(uint64, uint8) string
	workers         map[workerID]*hostWorker
	queueMu         sync.Mutex
	queue           []Input
	signal          chan struct{}
	closed          bool
	busy            atomic.Bool
	recorder        *s.Recorder
	published       []uint64
	settled         map[uint64]string
	diagnostics     []string
	executedEffects uint64
}

func NewHost(bounds Bounds, work func(uint64, uint8) string, record bool) (*Host, error) {
	if err := bounds.Validate(); err != nil {
		return nil, err
	}
	if work == nil {
		return nil, &s.ConfigError{Message: "nil controller work callback"}
	}
	h := &Host{model: Controller{Bounds: bounds}, work: work, workers: map[workerID]*hostWorker{}, signal: make(chan struct{}, 1), settled: map[uint64]string{}}
	if record {
		r, err := s.NewRecorder(h.model.Model(), h.state, s.RunConfig{Strategy: "reference-live-host"}, 1024)
		if err != nil {
			return nil, err
		}
		h.recorder = r
	}
	return h, nil
}

var ErrHostBusy = &s.ModelError{Message: "host operation already in progress"}

func (h *Host) begin() error {
	if !h.busy.CompareAndSwap(false, true) {
		return ErrHostBusy
	}
	return nil
}
func (h *Host) State() (State, error) {
	if err := h.begin(); err != nil {
		return State{}, err
	}
	defer h.busy.Store(false)
	return h.state.Clone(), nil
}
func (h *Host) Recording() (*s.AuditTrace, error) {
	if err := h.begin(); err != nil {
		return nil, err
	}
	defer h.busy.Store(false)
	if h.recorder == nil {
		return nil, nil
	}
	return h.recorder.Snapshot(), nil
}

type HostSnapshot struct {
	Published       []uint64
	Settled         map[uint64]string
	Diagnostics     []string
	ExecutedEffects uint64
	PendingWorkers  int
}

func (h *Host) Snapshot() (HostSnapshot, error) {
	if err := h.begin(); err != nil {
		return HostSnapshot{}, err
	}
	defer h.busy.Store(false)
	r := HostSnapshot{Published: append([]uint64(nil), h.published...), Settled: map[uint64]string{}, Diagnostics: append([]string(nil), h.diagnostics...), ExecutedEffects: h.executedEffects, PendingWorkers: len(h.workers)}
	for k, v := range h.settled {
		r.Settled[k] = v
	}
	return r, nil
}
func (h *Host) enqueue(in Input) {
	h.queueMu.Lock()
	defer h.queueMu.Unlock()
	if h.closed {
		return
	}
	h.queue = append(h.queue, in)
	select {
	case h.signal <- struct{}{}:
	default:
	}
}
func (h *Host) Deliver(in Input) ([]Effect, error) {
	if err := h.begin(); err != nil {
		return nil, err
	}
	defer h.busy.Store(false)
	return h.deliver(in)
}
func (h *Host) deliver(in Input) ([]Effect, error) {
	h.queueMu.Lock()
	closed := h.closed
	h.queueMu.Unlock()
	if closed {
		return nil, &s.ModelError{Message: "host closed"}
	}
	t, err := Reduce(h.state, in, h.model.Bounds)
	if err != nil {
		return nil, err
	}
	if h.recorder != nil {
		checks, e := s.ObserveRecorder(h.recorder, h.model.Model(), h.state, in, t)
		if e != nil {
			h.diagnostics = append(h.diagnostics, "recording unavailable: "+e.Error())
		} else {
			for _, check := range checks {
				if check.Status == "failed" {
					h.diagnostics = append(h.diagnostics, "property failure: "+check.ID)
				}
			}
		}
	}
	h.state = t.State
	for _, effect := range t.Outputs {
		if err = h.execute(effect); err != nil {
			return nil, err
		}
	}
	return append([]Effect(nil), t.Outputs...), nil
}
func (h *Host) execute(e Effect) error {
	h.executedEffects++
	switch e.Kind {
	case "start":
		id := workerID{e.Generation, e.Attempt}
		if _, ok := h.workers[id]; ok {
			return &s.ModelError{Message: "duplicate effect dispatch"}
		}
		w := &hostWorker{done: make(chan struct{})}
		h.workers[id] = w
		go func() {
			returned := false
			defer func() {
				if !returned {
					h.enqueue(Input{Kind: "complete", Generation: id.generation, Attempt: id.attempt, Outcome: "retryable_failure"})
				}
				close(w.done)
			}()
			outcome := "retryable_failure"
			func() {
				defer func() {
					if recover() != nil {
						outcome = "retryable_failure"
					}
				}()
				outcome = h.work(id.generation, id.attempt)
			}()
			if outcome != "success" && outcome != "retryable_failure" {
				outcome = "retryable_failure"
			}
			in := Input{Kind: "complete", Generation: id.generation, Attempt: id.attempt, Outcome: outcome}
			if w.cancelled.Load() {
				in = Input{Kind: "cancelled", Generation: id.generation, Attempt: id.attempt}
			}
			h.enqueue(in)
			returned = true
		}()
	case "cancel":
		if w := h.workers[workerID{e.Generation, e.Attempt}]; w != nil {
			w.cancelled.Store(true)
		}
	case "publish":
		h.published = append(h.published, e.Generation)
	case "settle":
		if _, ok := h.settled[e.Generation]; ok {
			return &s.ModelError{Message: "host saw duplicate settlement"}
		}
		h.settled[e.Generation] = e.Result
	case "release":
		if _, ok := h.settled[e.Generation]; !ok {
			return &s.ModelError{Message: "host saw release before settlement"}
		}
		for id, w := range h.workers {
			if id.generation == e.Generation {
				<-w.done
				delete(h.workers, id)
			}
		}
	default:
		return &s.ModelError{Message: fmt.Sprintf("unknown controller effect %q", e.Kind)}
	}
	return nil
}
func (h *Host) Receive(timeout time.Duration) (bool, error) {
	if err := h.begin(); err != nil {
		return false, err
	}
	defer h.busy.Store(false)
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	for {
		h.queueMu.Lock()
		if len(h.queue) > 0 {
			in := h.queue[0]
			h.queue[0] = Input{}
			h.queue = h.queue[1:]
			h.queueMu.Unlock()
			_, err := h.deliver(in)
			return true, err
		}
		closed := h.closed
		h.queueMu.Unlock()
		if closed {
			return false, &s.ModelError{Message: "host closed"}
		}
		select {
		case <-h.signal:
		case <-timer.C:
			return false, nil
		}
	}
}
func (h *Host) Tick(now uint64) error { _, err := h.Deliver(Input{Kind: "tick", Now: now}); return err }
func (h *Host) PendingWorkers() (int, error) {
	if err := h.begin(); err != nil {
		return 0, err
	}
	defer h.busy.Store(false)
	return len(h.workers), nil
}

// Close requests cooperative cancellation and detaches still-running workers;
// it does not assert that a worker or effect has settled.
func (h *Host) Close() error {
	if err := h.begin(); err != nil {
		return err
	}
	defer h.busy.Store(false)
	h.queueMu.Lock()
	h.closed = true
	h.queue = nil
	h.queueMu.Unlock()
	for _, w := range h.workers {
		w.cancelled.Store(true)
	}
	select {
	case h.signal <- struct{}{}:
	default:
	}
	return nil
}
