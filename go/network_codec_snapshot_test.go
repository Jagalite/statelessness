package statelessness

import (
	"bytes"
	"strings"
	"testing"
)

func TestNetworkCodecCapturesSlotsBeforeStateEncoder(t *testing.T) {
	child := Model[uint8, uint8, uint8]{InitialState: func() (uint8, error) { return 0, nil }, Step: func(s, i uint8) (Transition[uint8, uint8], error) { return Transition[uint8, uint8]{State: s}, nil }, CloneState: ValueCopy[uint8], CloneInput: ValueCopy[uint8], CloneOutput: ValueCopy[uint8], EqualStates: func(a, b uint8) bool { return a == b }, CheckState: func(uint8) ([]Check, error) { return nil, nil }}
	child.Codec = &Codec[uint8, uint8, uint8]{Metadata: func() (Metadata, error) { return Identity("child", "test"), nil }}
	n, err := NewNetwork([]NetworkAddress[Model[uint8, uint8, uint8]]{{1, child}, {2, child}}, NetworkWiring[uint8, uint8, uint8]{Routes: func(NetworkAddress[uint8]) ([]NetworkRoute[uint8], error) { return nil, nil }, CheckState: func(NetworkState[uint8, uint8], *CheckSink) error { return nil }}, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	state := NetworkState[uint8, uint8]{Machines: []NetworkAddress[uint8]{{1, 3}, {2, 4}}, Pending: []NetworkMessage[uint8]{{1, NetworkRoute[uint8]{2, 5, 6}}}}
	mutate := false
	base := Uint8Codec()
	layout, _ := base.Layout()
	sc, err := NewValueCodec("mutator", func(v uint8, b *EncodeBuffer) error {
		if mutate {
			state.Machines[1] = NetworkAddress[uint8]{1, 99}
			state.Pending[0].Route.Channel = 99
		}
		return base.EncodeRaw(v, b)
	}, base.DecodeRaw, &layout)
	if err != nil {
		t.Fatal(err)
	}
	if err = n.BindCodec(sc, base, base, func() (Metadata, error) { return Identity("wiring", "test"), nil }); err != nil {
		t.Fatal(err)
	}
	expected, err := n.Codec.EncodeState(state)
	if err != nil {
		t.Fatal(err)
	}
	mutate = true
	actual, err := n.Codec.EncodeState(state)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, expected) {
		t.Fatalf("encoding changed: got %x want %x", actual, expected)
	}
	// Even a small limit must fail at the original stream boundary rather than
	// allowing an encoder's mutation to replace the pending structural snapshot.
	state.Machines[1] = NetworkAddress[uint8]{2, 4}
	state.Pending = []NetworkMessage[uint8]{
		{1, NetworkRoute[uint8]{2, 5, 6}},
		{1, NetworkRoute[uint8]{2, 6, 7}},
		{1, NetworkRoute[uint8]{2, 7, 8}},
		{1, NetworkRoute[uint8]{2, 8, 9}},
	}
	var out []byte
	err = WithEncodeBuffer(&out, 31, func(b *EncodeBuffer) error { return n.Codec.EncodeStateInto(state, b) })
	if err == nil || !strings.Contains(err.Error(), "encoded blob exceeds byte limit") {
		t.Fatalf("got %v, want byte limit", err)
	}
	if len(out) != 31 || !bytes.Equal(out[27:31], []byte{5, 0, 0, 0}) {
		t.Fatalf("captured first channel was not retained at the output boundary: %x", out)
	}
}
