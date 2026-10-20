package statelessness

import "testing"

type unformattedValueError struct{ formats int }

func (e *unformattedValueError) Error() string { e.formats++; panic("must not format") }

func TestValueDecoderOwnsBuiltinStickyErrors(t *testing.T) {
	d, _ := NewDecoder(nil, DefaultDecodeLimits())
	_, err := d.Take(1)
	err.(*ModelError).Message = "changed"
	first := d.Finish().(*ModelError)
	if first.Message != "truncated value" {
		t.Fatal("returned failure mutated stored payload")
	}
	first.Message = "changed again"
	_, err = d.Take(0)
	if err.(*ModelError).Message != "truncated value" {
		t.Fatal("sticky return shared payload")
	}
	callbackError := &ModelError{"callback first"}
	codec, _ := NewValueCodec[Unit]("error", nil, func(*Decoder) (Unit, error) { return Unit{}, callbackError }, nil)
	d, _ = NewDecoder(nil, DefaultDecodeLimits())
	_, err = ReadValue(d, codec)
	callbackError.Message = "callback changed"
	err.(*ModelError).Message = "returned changed"
	if d.Finish().(*ModelError).Message != "callback first" {
		t.Fatal("callback error was borrowed")
	}
}
func TestValueDecoderPreservesOpaqueErrorWithoutFormatting(t *testing.T) {
	opaque := &unformattedValueError{}
	codec, _ := NewValueCodec[Unit]("opaque", nil, func(*Decoder) (Unit, error) { return Unit{}, opaque }, nil)
	d, _ := NewDecoder(nil, DefaultDecodeLimits())
	_, err := ReadValue(d, codec)
	if err != opaque || d.Finish() != opaque || opaque.formats != 0 {
		t.Fatal("opaque host error identity or formatting changed")
	}
}
