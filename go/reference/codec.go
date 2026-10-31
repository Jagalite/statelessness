package reference

import (
	"encoding/binary"
	s "github.com/Jagalite/statelessness/go"
	"math"
)

const stateByteLimit = 1 << 20

func codecError() error {
	return &s.ModelError{Message: "invalid or noncanonical reference-controller encoding"}
}
func put64(b []byte, v uint64) []byte { return binary.LittleEndian.AppendUint64(b, v) }
func phaseWire(j Job) (byte, uint64, error) {
	switch j.Phase {
	case "running":
		if j.Deadline == 0 && j.Result == "" {
			return 0, 0, nil
		}
	case "cancelling":
		if j.Deadline == 0 && j.Result == "" {
			return 1, 0, nil
		}
	case "waiting":
		if j.Result == "" {
			return 2, j.Deadline, nil
		}
	case "settled":
		if j.Deadline == 0 {
			switch j.Result {
			case "succeeded":
				return 3, 0, nil
			case "cancelled":
				return 4, 0, nil
			case "failed":
				return 5, 0, nil
			}
		}
	}
	return 0, 0, codecError()
}
func (c Controller) EncodeState(st State) ([]byte, error) {
	header := 13
	if st.Current != nil {
		header = 21
	}
	if len(st.Jobs) > math.MaxUint32 || len(st.Jobs) > (stateByteLimit-header)/19 {
		return nil, codecError()
	}
	out := put64(nil, st.Now)
	if st.Current == nil {
		out = append(out, 0)
	} else {
		out = append(out, 1)
		out = put64(out, *st.Current)
	}
	out = binary.LittleEndian.AppendUint32(out, uint32(len(st.Jobs)))
	for _, j := range st.Jobs {
		phase, deadline, err := phaseWire(j)
		if err != nil {
			return nil, err
		}
		out = put64(out, j.Generation)
		out = append(out, j.Attempt, phase)
		out = put64(out, deadline)
		if j.ResourceOwned {
			out = append(out, 1)
		} else {
			out = append(out, 0)
		}
	}
	if len(out) > stateByteLimit {
		return nil, codecError()
	}
	return out, nil
}
func (c Controller) DecodeState(data []byte) (State, error) {
	var zero State
	if len(data) > stateByteLimit || len(data) < 13 {
		return zero, codecError()
	}
	pos := 0
	take := func(n int) ([]byte, bool) {
		if n > len(data)-pos {
			return nil, false
		}
		v := data[pos : pos+n]
		pos += n
		return v, true
	}
	raw, _ := take(8)
	st := State{Now: binary.LittleEndian.Uint64(raw)}
	tag, _ := take(1)
	switch tag[0] {
	case 0:
	case 1:
		r, ok := take(8)
		if !ok {
			return zero, codecError()
		}
		v := binary.LittleEndian.Uint64(r)
		st.Current = &v
	default:
		return zero, codecError()
	}
	r, ok := take(4)
	if !ok {
		return zero, codecError()
	}
	count := uint64(binary.LittleEndian.Uint32(r))
	if count > 32768 || count > c.Bounds.Generations || count > uint64((len(data)-pos)/19) || st.Now > c.Bounds.Time {
		return zero, codecError()
	}
	if st.Current != nil && (*st.Current == 0 || *st.Current > count) {
		return zero, codecError()
	}
	st.Jobs = make([]Job, 0, int(count))
	for i := uint64(0); i < count; i++ {
		r, _ := take(19)
		g := binary.LittleEndian.Uint64(r[:8])
		attempt, phase, deadline, owned := r[8], r[9], binary.LittleEndian.Uint64(r[10:18]), r[18]
		if g != i+1 || attempt == 0 || attempt > c.Bounds.Attempts || owned > 1 {
			return zero, codecError()
		}
		j := Job{Generation: g, Attempt: attempt, ResourceOwned: owned == 1}
		switch phase {
		case 0:
			if deadline != 0 {
				return zero, codecError()
			}
			j.Phase = "running"
		case 1:
			if deadline != 0 {
				return zero, codecError()
			}
			j.Phase = "cancelling"
		case 2:
			if deadline > c.Bounds.Time {
				return zero, codecError()
			}
			j.Phase = "waiting"
			j.Deadline = deadline
		case 3, 4, 5:
			if deadline != 0 {
				return zero, codecError()
			}
			j.Phase = "settled"
			j.Result = []string{"succeeded", "cancelled", "failed"}[phase-3]
		default:
			return zero, codecError()
		}
		st.Jobs = append(st.Jobs, j)
	}
	if pos != len(data) {
		return zero, codecError()
	}
	return st, nil
}
func (c Controller) EncodeInput(in Input) ([]byte, error) {
	var tag, attempt, outcome byte
	var number uint64
	switch in.Kind {
	case "submit":
		if in.Generation != 0 || in.Attempt != 0 || in.Outcome != "" || in.Now != 0 {
			return nil, codecError()
		}
	case "cancel":
		tag = 1
		number = in.Generation
		if in.Attempt != 0 || in.Outcome != "" || in.Now != 0 {
			return nil, codecError()
		}
	case "complete":
		tag = 2
		number = in.Generation
		attempt = in.Attempt
		if in.Now != 0 {
			return nil, codecError()
		}
		switch in.Outcome {
		case "success":
		case "retryable_failure":
			outcome = 1
		default:
			return nil, codecError()
		}
	case "cancelled":
		tag = 3
		number = in.Generation
		attempt = in.Attempt
		if in.Outcome != "" || in.Now != 0 {
			return nil, codecError()
		}
	case "tick":
		tag = 4
		number = in.Now
		if in.Generation != 0 || in.Attempt != 0 || in.Outcome != "" {
			return nil, codecError()
		}
	default:
		return nil, codecError()
	}
	out := put64([]byte{tag}, number)
	return append(out, attempt, outcome), nil
}
func (c Controller) DecodeInput(data []byte) (Input, error) {
	if len(data) != 11 {
		return Input{}, codecError()
	}
	tag, n, a, o := data[0], binary.LittleEndian.Uint64(data[1:9]), data[9], data[10]
	switch {
	case tag == 0 && n == 0 && a == 0 && o == 0:
		return Input{Kind: "submit"}, nil
	case tag == 1 && n <= c.Bounds.Generations && a == 0 && o == 0:
		return Input{Kind: "cancel", Generation: n}, nil
	case tag == 2 && n <= c.Bounds.Generations && a > 0 && a <= c.Bounds.Attempts && o <= 1:
		outcome := "success"
		if o == 1 {
			outcome = "retryable_failure"
		}
		return Input{Kind: "complete", Generation: n, Attempt: a, Outcome: outcome}, nil
	case tag == 3 && n <= c.Bounds.Generations && a > 0 && a <= c.Bounds.Attempts && o == 0:
		return Input{Kind: "cancelled", Generation: n, Attempt: a}, nil
	case tag == 4 && n <= c.Bounds.Time && a == 0 && o == 0:
		return Input{Kind: "tick", Now: n}, nil
	}
	return Input{}, codecError()
}
func (c Controller) EncodeOutput(e Effect) ([]byte, error) {
	var tag, a, result byte
	switch e.Kind {
	case "start":
		a = e.Attempt
	case "cancel":
		tag = 1
		a = e.Attempt
	case "publish":
		tag = 2
	case "settle":
		tag = 3
		switch e.Result {
		case "succeeded":
		case "cancelled":
			result = 1
		case "failed":
			result = 2
		default:
			return nil, codecError()
		}
	case "release":
		tag = 4
	default:
		return nil, codecError()
	}
	if (e.Kind == "start" || e.Kind == "cancel") && e.Result != "" || (e.Kind != "start" && e.Kind != "cancel") && e.Attempt != 0 || e.Kind != "settle" && e.Result != "" {
		return nil, codecError()
	}
	return append(put64([]byte{tag}, e.Generation), a, result), nil
}
