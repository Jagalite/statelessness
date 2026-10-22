// The fixture supplies only model callbacks and host authority. The public
// debugger library owns every request, response and transport operation.
package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func decodeInput(bytes []byte) (byte, error) {
	fmt.Fprintf(os.Stderr, "CALL decode %x\n", bytes)
	if len(bytes) != 1 || bytes[0] > 3 {
		return 0, fmt.Errorf("invalid fixture input")
	}
	return bytes[0], nil
}

func encodeU64(value uint64, out *s.EncodeBuffer) error {
	var bytes [8]byte
	binary.LittleEndian.PutUint64(bytes[:], value)
	return out.Append(bytes[:])
}

func run() error {
	authority := d.ProtocolAuthority{DeliverInputs: true}
	limits := d.DefaultProtocolLimits()
	for _, arg := range os.Args[1:] {
		switch arg {
		case "--read-only":
			authority.DeliverInputs = false
		case "--exact":
			authority.ReadExactValues = true
		case "--cache-one":
			limits.MaxCache = 1
		default:
			return fmt.Errorf("unknown argument: %s", arg)
		}
	}
	model := s.Model[uint64, byte, uint64]{
		InitialState: func() (uint64, error) {
			fmt.Fprintln(os.Stderr, "CALL initial")
			return 0, nil
		},
		CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64],
		Step: func(before uint64, input byte) (s.Transition[uint64, uint64], error) {
			fmt.Fprintf(os.Stderr, "CALL step %d %d\n", before, input)
			switch input {
			case 0:
				return s.Transition[uint64, uint64]{State: before, Disposition: s.Ignored("fixture ignored")}, nil
			case 1:
				return s.Accept(before+1, before+1), nil
			case 2:
				return s.Transition[uint64, uint64]{State: before, Disposition: s.Rejected("fixture rejection")}, nil
			default:
				return s.Transition[uint64, uint64]{}, fmt.Errorf("private reducer detail")
			}
		},
		CheckState: func(state uint64) ([]s.Check, error) {
			fmt.Fprintf(os.Stderr, "CALL state %d\n", state)
			if state > 2 {
				return []s.Check{s.Failed("bounded", "private state detail")}, nil
			}
			return []s.Check{s.Passed("bounded")}, nil
		},
		CheckTransition: func(before uint64, input byte, after s.Transition[uint64, uint64]) ([]s.Check, error) {
			fmt.Fprintf(os.Stderr, "CALL transition %d %d %d\n", before, input, after.State)
			return []s.Check{s.Passed("transition")}, nil
		},
		Inputs: func(uint64) (s.Iterator[byte], error) { return s.Values([]byte{0, 1, 2, 3}), nil },
		EqualStates: func(a, b uint64) bool { return a == b },
		Codec: &s.Codec[uint64, byte, uint64]{
			Metadata: func() (s.Metadata, error) { return s.Identity("n12-wire-counter", "n12-wire-v1"), nil },
			EncodeStateInto: encodeU64,
			DecodeState: func(bytes []byte) (uint64, error) {
				if len(bytes) != 8 {
					return 0, fmt.Errorf("invalid fixture state")
				}
				return binary.LittleEndian.Uint64(bytes), nil
			},
			EncodeInputInto: func(input byte, out *s.EncodeBuffer) error { return out.Append([]byte{input}) },
			DecodeInput: decodeInput,
			EncodeOutputInto: encodeU64,
		},
	}
	session, err := d.NewSession("wire\té😀", model, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		return err
	}
	protocol, err := d.NewProtocolSession(session, model.Codec.DecodeInput, authority, limits)
	if err != nil {
		return err
	}
	// Let a real broken stdout pipe return through Serve so the fixture can
	// observe the actual committed session before reporting transport failure.
	signal.Ignore(syscall.SIGPIPE)
	err = protocol.Serve(os.Stdin, os.Stdout)
	fmt.Fprintf(os.Stderr, "FINAL %d %d %d\n", session.State(), session.Revision(), session.Sequence())
	return err
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
