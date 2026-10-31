package debug

import (
	"math"
	"strings"
	"testing"
)

func TestProtocolFieldPreflightBoundaries(t *testing.T) {
	l := DefaultProtocolLimits()
	l.MaxFrame = 2048
	l.MaxField = 2048
	p, e := encodeFields([]string{strings.Repeat("a", 2045), "\t"}, l)
	if e == nil || p != nil {
		t.Fatalf("oversized frame accepted: %d %v", len(p), e)
	}
	p, e = encodeFields([]string{strings.Repeat("a", 2044), "\t"}, l)
	if e != nil || len(p) != 2048 {
		t.Fatalf("exact frame: %d %v", len(p), e)
	}
	l.MaxFields = 64
	_, e = decodeFields([]byte(strings.Repeat("\t", 64)), l)
	if e == nil {
		t.Fatal("excess field count accepted")
	}
}

func TestProtocolLimitsDoNotOverflow(t *testing.T) {
	l := DefaultProtocolLimits()
	l.MaxFields, l.MaxField = math.MaxInt, math.MaxInt
	req := ProtocolRequest{Session: "s", Command: "status"}
	b, e := EncodeRequest(req, l)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = DecodeRequest(b, l); e != nil {
		t.Fatal(e)
	}
	res := ProtocolResponse{Session: "s", Fields: []ProtocolField{{"k", "v"}}}
	b, e = EncodeResponse(res, l)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = DecodeResponse(b, l); e != nil {
		t.Fatal(e)
	}
	l.MaxFields = -1
	if _, e = DecodeResponse(b, l); e == nil {
		t.Fatal("negative field limit accepted")
	}
}

func TestProtocolRejectsOversizedCollectionsBeforeAssembly(t *testing.T) {
	l := DefaultProtocolLimits()
	r := ProtocolRequest{Session: "s", Command: "status", Args: make([]string, 56)}
	if _, e := EncodeRequest(r, l); e == nil {
		t.Fatal("too many request fields")
	}
	l.MaxFields = math.MaxInt
	response := ProtocolResponse{Session: "s", Fields: make([]ProtocolField, l.MaxFrame)}
	if _, e := EncodeResponse(response, l); e == nil {
		t.Fatal("too many fields for frame")
	}
}
