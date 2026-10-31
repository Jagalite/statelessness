package debug_test

import (
	"bytes"
	"errors"
	"io"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type frameReaderFunc func([]byte) (int, error)

func (f frameReaderFunc) Read(p []byte) (int, error) { return f(p) }

func TestFrameReaderInvalidCountsAndNoProgress(t *testing.T) {
	for _, prefix := range [][]byte{nil, {0, 0, 0, 3}} {
		for _, count := range []int{-1, 1} {
			r := io.MultiReader(bytes.NewReader(prefix), frameReaderFunc(func(p []byte) (int, error) {
				if count < 0 {
					return -1, nil
				}
				return len(p) + 1, nil
			}))
			if _, err := d.ReadFrame(r, 10); err == nil {
				t.Fatal("accepted invalid reader count", prefix, count)
			}
		}
		calls := 0
		r := io.MultiReader(bytes.NewReader(prefix), frameReaderFunc(func([]byte) (int, error) { calls++; return 0, nil }))
		if _, err := d.ReadFrame(r, 10); !errors.Is(err, io.ErrNoProgress) || calls != 100 {
			t.Fatalf("prefix %v: error %v, calls %d", prefix, err, calls)
		}
	}
}

func TestFrameReaderPartialAndEOFBehavior(t *testing.T) {
	cases := []struct {
		input []byte
		want  error
	}{
		{nil, io.EOF}, {[]byte{0, 0}, io.ErrUnexpectedEOF}, {[]byte{0, 0, 0, 3}, io.ErrUnexpectedEOF}, {[]byte{0, 0, 0, 3, 'a'}, io.ErrUnexpectedEOF},
	}
	for _, tc := range cases {
		if _, err := d.ReadFrame(bytes.NewReader(tc.input), 10); !errors.Is(err, tc.want) {
			t.Fatalf("%v: %v", tc.input, err)
		}
	}
	data := []byte{0, 0, 0, 3, 'a', 'b', 'c'}
	r := frameReaderFunc(func(p []byte) (int, error) {
		if len(data) == 0 {
			return 0, io.EOF
		}
		p[0] = data[0]
		data = data[1:]
		if len(data) == 0 {
			return 1, io.EOF
		}
		return 1, nil
	})
	got, err := d.ReadFrame(r, 10)
	if err != nil || string(got) != "abc" {
		t.Fatalf("got %q, %v", got, err)
	}
}
