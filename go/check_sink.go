package statelessness

import "fmt"

// CheckSink is an append-only, synchronous callback lease. It never exposes its
// backing slice. Each invocation (including nested adapters) gets a fresh lease,
// revoked on success, error, and panic. Retaining/concurrently using it is invalid.
// Every expired operation fails before reading or changing current storage.
type CheckSink struct{ lease *checkLease }
type checkBatch struct {
	checks  *[]Check
	failure error
}
type checkLease struct {
	batch  *checkBatch
	active bool
}

func (s *CheckSink) storage() (*[]Check, error) {
	if s == nil || s.lease == nil || !s.lease.active {
		return nil, &ModelError{"check sink lease expired"}
	}
	return s.lease.batch.checks, nil
}
func (s *CheckSink) fail(err error) error {
	if s.lease.batch.failure == nil {
		s.lease.batch.failure = err
	}
	return s.lease.batch.failure
}
func (s *CheckSink) Push(check Check) error {
	storage, e := s.storage()
	if e != nil {
		return e
	}
	if s.lease.batch.failure != nil {
		return s.lease.batch.failure
	}
	if e := check.Validate(); e != nil {
		return s.fail(&ModelError{fmt.Sprintf("invalid appended check: %v", e)})
	}
	*storage = append(*storage, check)
	return nil
}
func (s *CheckSink) Extend(checks []Check) error {
	if _, e := s.storage(); e != nil {
		return e
	}
	if s.lease.batch.failure != nil {
		return s.lease.batch.failure
	}
	for _, check := range checks {
		if e := s.Push(check); e != nil {
			return e
		}
	}
	return nil
}
func (s *CheckSink) Snapshot() ([]Check, error) {
	storage, e := s.storage()
	if e != nil {
		return nil, e
	}
	return copyChecks(*storage), nil
}
func (s *CheckSink) Len() (int, error) {
	storage, e := s.storage()
	if e != nil {
		return 0, e
	}
	return len(*storage), nil
}
func (s *CheckSink) IsEmpty() (bool, error) { length, e := s.Len(); return length == 0, e }
func (s *CheckSink) Reserve(additional int) (err error) {
	storage, e := s.storage()
	if e != nil {
		return e
	}
	if s.lease.batch.failure != nil {
		return s.lease.batch.failure
	}
	maximum := int(^uint(0) >> 1)
	if additional < 0 || additional > maximum-len(*storage) {
		return s.fail(&ModelError{"invalid check reserve"})
	}
	defer func() {
		if p := recover(); p != nil {
			err = s.fail(&ModelError{fmt.Sprintf("check reserve: panic: %v", p)})
		}
	}()
	needed := len(*storage) + additional
	if needed > cap(*storage) {
		grown := make([]Check, len(*storage), needed)
		copy(grown, *storage)
		*storage = grown
	}
	return nil
}
func invokeChecks(stage string, storage *[]Check, callback func(*CheckSink) error) error {
	return invokeCheckBatch(stage, &checkBatch{checks: storage}, callback)
}
func invokeChildChecks(stage string, parent *CheckSink, callback func(*CheckSink) error) error {
	if _, e := parent.storage(); e != nil {
		return e
	}
	return invokeCheckBatch(stage, parent.lease.batch, callback)
}

// checkFailureContext keeps native ModelError classification and stage text while
// retaining identity of the first sticky failure for errors.Is/As traversal.
type checkFailureContext struct {
	reported error
	primary  error
}

func (e *checkFailureContext) Error() string   { return e.reported.Error() }
func (e *checkFailureContext) Unwrap() []error { return []error{e.reported, e.primary} }

func invokeCheckBatch(stage string, batch *checkBatch, callback func(*CheckSink) error) error {
	if batch.failure != nil {
		return batch.failure
	}
	lease := &checkLease{batch: batch, active: true}
	sink := &CheckSink{lease: lease}
	defer func() { lease.active = false; lease.batch = nil }()
	_, e := call(stage, func() (struct{}, error) {
		callbackError := callback(sink)
		if batch.failure != nil {
			return struct{}{}, batch.failure
		}
		return struct{}{}, callbackError
	})
	// Recovery can return a later panic before the callback's normal sticky-error
	// check runs. The already-recorded first failure still takes precedence.
	if batch.failure != nil {
		return &checkFailureContext{reported: &ModelError{fmt.Sprintf("%s: %v", stage, batch.failure)}, primary: batch.failure}
	}
	if e != nil {
		batch.failure = e
		return e
	}
	return nil
}

func legacyChecks(sink *CheckSink, callback func() ([]Check, error)) error {
	checks, callbackError := callback()
	priorFailure := sink.lease.batch.failure
	appendError := sink.Extend(checks)
	if callbackError != nil {
		// The legacy callback failed before its returned values were validated.
		// Keep its primary error while retaining only the validated returned prefix.
		if priorFailure == nil {
			sink.lease.batch.failure = callbackError
		}
		return callbackError
	}
	return appendError
}
func stateChecksInto[S, I, O any](m Model[S, I, O], state S, storage *[]Check) error {
	return stateChecksBatch(m, state, &checkBatch{checks: storage})
}
func stateChecksBatch[S, I, O any](m Model[S, I, O], state S, batch *checkBatch) error {
	return stateChecksStageBatch("state check", m, state, batch)
}
func stateChecksStageBatch[S, I, O any](stage string, m Model[S, I, O], state S, batch *checkBatch) error {
	return invokeCheckBatch(stage, batch, func(sink *CheckSink) error {
		snapshot := m.CloneState(state)
		if m.CheckStateInto != nil {
			return m.CheckStateInto(snapshot, sink)
		}
		return legacyChecks(sink, func() ([]Check, error) { return m.CheckState(snapshot) })
	})
}
func edgeChecksInto[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O], storage *[]Check) error {
	return edgeChecksBatch(m, before, input, t, &checkBatch{checks: storage})
}
func edgeChecksBatch[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O], batch *checkBatch) error {
	if m.CheckTransitionInto == nil && m.CheckTransition == nil {
		return nil
	}
	return invokeCheckBatch("transition check", batch, func(sink *CheckSink) error {
		snapshot, e := cloneTransition(m, t)
		if e != nil {
			return e
		}
		b, i := m.CloneState(before), m.CloneInput(input)
		if m.CheckTransitionInto != nil {
			return m.CheckTransitionInto(b, i, snapshot, sink)
		}
		return legacyChecks(sink, func() ([]Check, error) { return m.CheckTransition(b, i, snapshot) })
	})
}
func appendStateChecks[S, I, O any](m Model[S, I, O], state S, sink *CheckSink) error {
	if _, e := sink.storage(); e != nil {
		return e
	}
	return stateChecksBatch(m, state, sink.lease.batch)
}
func appendEdgeChecks[S, I, O any](m Model[S, I, O], state S, input I, t Transition[S, O], sink *CheckSink) error {
	if _, e := sink.storage(); e != nil {
		return e
	}
	return edgeChecksBatch(m, state, input, t, sink.lease.batch)
}

func resetChecks(storage *[]Check) { clear(*storage); *storage = (*storage)[:0] }

// WithCheckSink lends an append-only lease over existing caller storage without
// clearing its prefix. Caller storage is exclusive for this synchronous call:
// do not mutate an alias, reenter on the same buffer, or use it concurrently.
func WithCheckSink(checks *[]Check, callback func(*CheckSink) error) error {
	if checks == nil || callback == nil {
		return &ConfigError{"check sink requires storage and callback"}
	}
	for _, check := range *checks {
		if e := check.Validate(); e != nil {
			return &ModelError{fmt.Sprintf("invalid existing check: %v", e)}
		}
	}
	return invokeChecks("check sink callback", checks, callback)
}

// AppendStateChecks chooses the preferred checker and lends it a fresh child
// lease, preserving preceding observations and the caller's active outer lease.
func (m Model[S, I, O]) AppendStateChecks(state S, sink *CheckSink) error {
	return appendStateChecks(m, state, sink)
}
func (m Model[S, I, O]) AppendTransitionChecks(before S, input I, t Transition[S, O], sink *CheckSink) error {
	return appendEdgeChecks(m, before, input, t, sink)
}
