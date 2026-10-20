package statelessness

import (
	"encoding/binary"
	"fmt"
)

// EncodeBuffer is a synchronous revocable codec lease over one shared bounded
// destination. Incoming chunks are copied only after whole-chunk admission.
// Existing backing capacity may exceed the logical limit; this is not an RSS cap.
type EncodeBuffer struct{ lease *encodeLease }
type encodeBatch struct {
	bytes   *[]byte
	maximum uint64
	failure error
}
type encodeLease struct {
	batch  *encodeBatch
	active bool
}

func (e *EncodeBuffer) batch() (*encodeBatch, error) {
	if e == nil || e.lease == nil || !e.lease.active {
		return nil, &ModelError{"encode buffer lease expired"}
	}
	return e.lease.batch, nil
}
func encodeFailure(batch *encodeBatch, err error) error {
	if batch.failure == nil {
		batch.failure = err
	}
	return batch.failure
}
func (e *EncodeBuffer) Append(chunk []byte) (err error) {
	batch, err := e.batch()
	if err != nil {
		return err
	}
	if batch.failure != nil {
		return batch.failure
	}
	current := uint64(len(*batch.bytes))
	incoming := uint64(len(chunk))
	maxInt := uint64(int(^uint(0) >> 1))
	if current > batch.maximum || incoming > batch.maximum-current || incoming > maxInt-current {
		return encodeFailure(batch, &ModelError{"encoded blob exceeds byte limit"})
	}
	defer func() {
		if p := recover(); p != nil {
			err = encodeFailure(batch, &ModelError{fmt.Sprintf("unable to allocate encoded blob: %v", p)})
		}
	}()
	required := len(*batch.bytes) + len(chunk)
	if required > cap(*batch.bytes) {
		capacity := uint64(cap(*batch.bytes))
		if capacity > maxInt/2 {
			capacity = maxInt
		} else {
			capacity *= 2
		}
		capacity = min(max(capacity, uint64(required), 64), batch.maximum, maxInt)
		grown := make([]byte, len(*batch.bytes), int(capacity))
		copy(grown, *batch.bytes)
		*batch.bytes = grown
	}
	*batch.bytes = append(*batch.bytes, chunk...)
	return nil
}
func (e *EncodeBuffer) ExtendFromSlice(chunk []byte) error { return e.Append(chunk) }

// ExtendFromVec copies an already-allocated legacy result for ownership safety;
// its source allocation happened outside this incremental sink's limit.
func (e *EncodeBuffer) ExtendFromVec(chunk []byte) error { return e.Append(chunk) }
func (e *EncodeBuffer) Len() (int, error) {
	batch, err := e.batch()
	if err != nil {
		return 0, err
	}
	return len(*batch.bytes), nil
}

// Snapshot returns a detached successful encoding prefix only while the lease
// is healthy. Its allocation is additional to the logical destination budget.
func (e *EncodeBuffer) Snapshot() (out []byte, err error) {
	batch, err := e.batch()
	if err != nil {
		return nil, err
	}
	if batch.failure != nil {
		return nil, batch.failure
	}
	defer func() {
		if p := recover(); p != nil {
			out = nil
			err = encodeFailure(batch, &ModelError{fmt.Sprintf("unable to allocate encoded snapshot: %v", p)})
		}
	}()
	return append([]byte{}, (*batch.bytes)...), nil
}

func (e *EncodeBuffer) Finish() error {
	batch, err := e.batch()
	if err != nil {
		return err
	}
	return batch.failure
}
func (e *EncodeBuffer) Framed(callback func(*EncodeBuffer) error) error {
	batch, err := e.batch()
	if err != nil {
		return err
	}
	if batch.failure != nil {
		return batch.failure
	}
	if callback == nil {
		return encodeFailure(batch, &ConfigError{"nil framed encoder"})
	}
	start := len(*batch.bytes)
	if err = e.Append(make([]byte, 8)); err != nil {
		return err
	}
	if err = invokeEncodeBatch("framed encode", batch, callback); err != nil {
		return encodeFailure(batch, err)
	}
	if err = e.Finish(); err != nil {
		return err
	}
	length := uint64(len(*batch.bytes) - start - 8)
	binary.LittleEndian.PutUint64((*batch.bytes)[start:start+8], length)
	return nil
}
func invokeEncodeBatch(stage string, batch *encodeBatch, callback func(*EncodeBuffer) error) error {
	if batch.failure != nil {
		return batch.failure
	}
	lease := &encodeLease{batch: batch, active: true}
	buffer := &EncodeBuffer{lease: lease}
	defer func() { lease.active = false; lease.batch = nil }()
	var callbackError error
	_, err := call(stage, func() (struct{}, error) {
		callbackError = callback(buffer)
		if callbackError != nil {
			return struct{}{}, callbackError
		}
		return struct{}{}, batch.failure
	})
	if err != nil {
		return encodeFailure(batch, err)
	}
	return batch.failure
}
func WithEncodeBuffer(bytes *[]byte, maximum uint64, callback func(*EncodeBuffer) error) error {
	if bytes == nil || callback == nil {
		return &ConfigError{"encode buffer requires storage and callback"}
	}
	*bytes = (*bytes)[:0]
	return invokeEncodeBatch("encode buffer callback", &encodeBatch{bytes: bytes, maximum: maximum}, callback)
}
func appendEncoded[T any](value T, clone func(T) T, legacy func(T) ([]byte, error), into func(T, *EncodeBuffer) error, buffer *EncodeBuffer) error {
	batch, err := buffer.batch()
	if err != nil {
		return err
	}
	return invokeEncodeBatch("encode callback", batch, func(child *EncodeBuffer) error {
		snapshot := clone(value)
		if into != nil {
			return into(snapshot, child)
		}
		if legacy == nil {
			return &ModelError{"missing encoder callback"}
		}
		encoded, err := legacy(snapshot)
		if err != nil {
			return err
		}
		return child.ExtendFromVec(encoded)
	})
}
func encodeStateInto[S, I, O any](m Model[S, I, O], state S, bytes *[]byte, maximum uint64) error {
	return WithEncodeBuffer(bytes, maximum, func(buffer *EncodeBuffer) error {
		return appendEncoded(state, m.CloneState, m.Codec.EncodeState, m.Codec.EncodeStateInto, buffer)
	})
}
func encodeInputInto[S, I, O any](m Model[S, I, O], input I, bytes *[]byte, maximum uint64) error {
	return WithEncodeBuffer(bytes, maximum, func(buffer *EncodeBuffer) error {
		return appendEncoded(input, m.CloneInput, m.Codec.EncodeInput, m.Codec.EncodeInputInto, buffer)
	})
}
func encodeOutputInto[S, I, O any](m Model[S, I, O], output O, bytes *[]byte, maximum uint64) error {
	return WithEncodeBuffer(bytes, maximum, func(buffer *EncodeBuffer) error {
		return appendEncoded(output, m.CloneOutput, m.Codec.EncodeOutput, m.Codec.EncodeOutputInto, buffer)
	})
}
func encodeState[S, I, O any](m Model[S, I, O], state S, maximum int) ([]byte, error) {
	var bytes []byte
	err := encodeStateInto(m, state, &bytes, uint64(maximum))
	if err != nil {
		return nil, err
	}
	return bytes, nil
}
func encodeInput[S, I, O any](m Model[S, I, O], input I, maximum int) ([]byte, error) {
	var bytes []byte
	err := encodeInputInto(m, input, &bytes, uint64(maximum))
	if err != nil {
		return nil, err
	}
	return bytes, nil
}
func encodeOutput[S, I, O any](m Model[S, I, O], output O, maximum int) ([]byte, error) {
	var bytes []byte
	err := encodeOutputInto(m, output, &bytes, uint64(maximum))
	if err != nil {
		return nil, err
	}
	return bytes, nil
}

func (m Model[S, I, O]) AppendStateEncoding(state S, buffer *EncodeBuffer) error {
	if m.Codec == nil {
		return &ModelError{"missing codec"}
	}
	return appendEncoded(state, m.CloneState, m.Codec.EncodeState, m.Codec.EncodeStateInto, buffer)
}
func (m Model[S, I, O]) AppendInputEncoding(input I, buffer *EncodeBuffer) error {
	if m.Codec == nil {
		return &ModelError{"missing codec"}
	}
	return appendEncoded(input, m.CloneInput, m.Codec.EncodeInput, m.Codec.EncodeInputInto, buffer)
}
func (m Model[S, I, O]) AppendOutputEncoding(output O, buffer *EncodeBuffer) error {
	if m.Codec == nil {
		return &ModelError{"missing codec"}
	}
	return appendEncoded(output, m.CloneOutput, m.Codec.EncodeOutput, m.Codec.EncodeOutputInto, buffer)
}
