package statelessness_test

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"math/big"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type valueSchema struct {
	Kind          string
	Element       *valueSchema
	Fields        []valueSchema
	Length        uint64
	LengthWidth   *uint8 `json:"length_width"`
	Model, Oracle *valueSchema
}
type valueFixture struct {
	Case           string
	Schema         valueSchema
	Entry          string
	Value          any
	BytesHex       string `json:"bytes_hex"`
	EncodeMaxBytes string `json:"encode_max_bytes"`
	Limits         map[string]string
	Expected       struct {
		Value any
		Error *string
	}
}

func valueLimits(t *testing.T, v map[string]string) s.DecodeLimits {
	t.Helper()
	limits := s.DefaultDecodeLimits()
	for key, value := range v {
		n, e := strconv.ParseUint(value, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		switch key {
		case "bytes":
			limits.Bytes = n
		case "allocation":
			limits.Allocation = n
		case "elements":
			limits.Elements = n
		case "depth":
			limits.Depth = n
		case "work":
			limits.Work = n
		default:
			t.Fatal(key)
		}
	}
	return limits
}
func mustValueCodec[T any](t *testing.T, schema string, encode func(T, *s.EncodeBuffer) error, decode func(*s.Decoder) (T, error), layout *s.ValueLayout) s.ValueCodec[T] {
	t.Helper()
	codec, e := s.NewValueCodec(schema, encode, decode, layout)
	if e != nil {
		t.Fatal(e)
	}
	return codec
}
func erasedValueCodec[T any](t *testing.T, codec s.ValueCodec[T], into func(any) T, from func(T) any) s.ValueCodec[any] {
	t.Helper()
	var layout *s.ValueLayout
	if l, ok := codec.Layout(); ok {
		layout = &l
	}
	return mustValueCodec(t, "corpus-erased", func(v any, b *s.EncodeBuffer) error { return codec.EncodeRaw(into(v), b) }, func(d *s.Decoder) (any, error) {
		v, e := codec.DecodeRaw(d)
		if e != nil {
			return nil, e
		}
		return from(v), nil
	}, layout)
}
func decimalInteger[T ~uint8 | ~uint16 | ~uint32 | ~uint64 | ~int8 | ~int16 | ~int32 | ~int64](codec s.ValueCodec[T], t *testing.T) s.ValueCodec[any] {
	return erasedValueCodec(t, codec, func(v any) T {
		n, ok := new(big.Int).SetString(v.(string), 10)
		if !ok {
			t.Fatal("invalid decimal")
		}
		if n.Sign() < 0 {
			return T(n.Int64())
		}
		return T(n.Uint64())
	}, func(v T) any { return fmt.Sprint(v) })
}
func valueBig(t *testing.T, v any) *big.Int {
	t.Helper()
	n, ok := new(big.Int).SetString(v.(string), 10)
	if !ok {
		t.Fatal("invalid integer")
	}
	return n
}
func u128Value(t *testing.T, v any) s.Uint128 {
	n := valueBig(t, v)
	return s.Uint128{Lo: n.Uint64(), Hi: new(big.Int).Rsh(new(big.Int).Set(n), 64).Uint64()}
}
func formatU128(v s.Uint128) string {
	n := new(big.Int).SetUint64(v.Hi)
	n.Lsh(n, 64)
	n.Or(n, new(big.Int).SetUint64(v.Lo))
	return n.String()
}
func i128Value(t *testing.T, v any) s.Int128 {
	n := valueBig(t, v)
	if n.Sign() < 0 {
		n.Add(n, new(big.Int).Lsh(big.NewInt(1), 128))
	}
	return s.Int128{Lo: n.Uint64(), Hi: int64(new(big.Int).Rsh(new(big.Int).Set(n), 64).Uint64())}
}
func formatI128(v s.Int128) string {
	n := new(big.Int).SetUint64(uint64(v.Hi))
	n.Lsh(n, 64)
	n.Or(n, new(big.Int).SetUint64(v.Lo))
	if v.Hi < 0 {
		n.Sub(n, new(big.Int).Lsh(big.NewInt(1), 128))
	}
	return n.String()
}
func valueCodecFor(t *testing.T, shape valueSchema) s.ValueCodec[any] {
	t.Helper()
	switch shape.Kind {
	case "u8":
		return decimalInteger(s.Uint8Codec(), t)
	case "u16":
		return decimalInteger(s.Uint16Codec(), t)
	case "u32":
		return decimalInteger(s.Uint32Codec(), t)
	case "u64":
		return decimalInteger(s.Uint64Codec(), t)
	case "i8":
		return decimalInteger(s.Int8Codec(), t)
	case "i16":
		return decimalInteger(s.Int16Codec(), t)
	case "i32":
		return decimalInteger(s.Int32Codec(), t)
	case "i64":
		return decimalInteger(s.Int64Codec(), t)
	case "u128":
		return erasedValueCodec(t, s.Uint128Codec(), func(v any) s.Uint128 { return u128Value(t, v) }, func(v s.Uint128) any { return formatU128(v) })
	case "i128":
		return erasedValueCodec(t, s.Int128Codec(), func(v any) s.Int128 { return i128Value(t, v) }, func(v s.Int128) any { return formatI128(v) })
	case "bool":
		return erasedValueCodec(t, s.BoolCodec(), func(v any) bool { return v.(bool) }, func(v bool) any { return v })
	case "unit":
		return erasedValueCodec(t, s.UnitCodec(), func(any) s.Unit { return s.Unit{} }, func(s.Unit) any { return nil })
	case "string":
		codec := s.StringCodec()
		if shape.LengthWidth != nil {
			var e error
			codec, e = s.StringLengthCodec(*shape.LengthWidth)
			if e != nil {
				t.Fatal(e)
			}
		}
		return erasedValueCodec(t, codec, func(v any) string { return v.(string) }, func(v string) any { return v })
	}
	return valueCompositeFor(t, shape)
}
func valueDecodeEntry(t *testing.T, shape valueSchema, entry string, data []byte, limits s.DecodeLimits) (any, error) {
	t.Helper()
	codec := valueCodecFor(t, shape)
	d, e := s.NewDecoder(data, limits)
	if e != nil {
		return nil, e
	}
	var value any
	if entry == "direct_length" {
		value, e = valueDecodeLength(t, d, shape, nil)
	} else {
		value, e = s.ReadValue(d, codec)
	}
	if e != nil {
		return nil, e
	}
	if e = d.Finish(); e != nil {
		return nil, e
	}
	return value, nil
}
func compareValueResult(t *testing.T, value any, err error, want any, wantError *string) {
	t.Helper()
	if wantError != nil {
		if err == nil || err.Error() != *wantError {
			t.Fatalf("error=%v want %q", err, *wantError)
		}
		return
	}
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(value)
	b, _ := json.Marshal(want)
	if !bytes.Equal(a, b) {
		t.Fatalf("value=%s want=%s", a, b)
	}
}
func TestCanonicalValueSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_VALUE_CORPUS")
	if path == "" {
		path = "../conformance/canonical-value-codec-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Values       []valueFixture       `json:"value_cases"`
		Errors       []valueFixture       `json:"decode_error_cases"`
		EncodeErrors []valueFixture       `json:"encode_error_cases"`
		Readers      []valueReaderCase    `json:"reader_cases"`
		Lengths      []valueLengthCase    `json:"length_cases"`
		Native       []valueAdmissionCase `json:"native_admission_cases"`
		Encoders     []valueEncoderCase   `json:"encoder_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Values) < 83 || len(corpus.Errors) < 30 || len(corpus.Readers) < 19 {
		t.Fatal("incomplete value corpus")
	}
	for _, v := range corpus.Values {
		t.Run("value/"+v.Case, func(t *testing.T) {
			wire, e := hex.DecodeString(v.BytesHex)
			if e != nil {
				t.Fatal(e)
			}
			maximum, e := strconv.ParseUint(v.EncodeMaxBytes, 10, 64)
			if e != nil {
				t.Fatal(e)
			}
			encoded, e := s.EncodeValue(valueCodecFor(t, v.Schema), v.Value, maximum)
			if e != nil || !bytes.Equal(encoded, wire) {
				t.Fatalf("encoded=%x want=%x error=%v", encoded, wire, e)
			}
			value, e := valueDecodeEntry(t, v.Schema, v.Entry, wire, valueLimits(t, v.Limits))
			compareValueResult(t, value, e, v.Expected.Value, v.Expected.Error)
			for prefix := 0; prefix < len(wire); prefix++ {
				_, e = valueDecodeEntry(t, v.Schema, v.Entry, wire[:prefix], valueLimits(t, v.Limits))
				if e == nil || e.Error() != "truncated value" {
					t.Fatalf("prefix%d error=%v", prefix, e)
				}
			}
			trailing := append(append([]byte{}, wire...), 0)
			_, e = valueDecodeEntry(t, v.Schema, v.Entry, trailing, s.DefaultDecodeLimits())
			if e == nil || e.Error() != "trailing value bytes" {
				t.Fatalf("trailing error=%v", e)
			}
			storage := append([]byte{0xfe, 0xfd}, wire...)
			storage = append(storage, 0xfc)
			view := storage[2 : 2+len(wire)]
			owned, e := valueDecodeEntry(t, v.Schema, v.Entry, view, s.DefaultDecodeLimits())
			if e != nil {
				t.Fatal(e)
			}
			for i := range view {
				view[i] = 0
			}
			compareValueResult(t, owned, nil, v.Expected.Value, nil)
		})
	}
	for _, v := range corpus.Errors {
		t.Run("decode/"+v.Case, func(t *testing.T) {
			wire, e := hex.DecodeString(v.BytesHex)
			if e != nil {
				t.Fatal(e)
			}
			value, e := valueDecodeEntry(t, v.Schema, v.Entry, wire, valueLimits(t, v.Limits))
			compareValueResult(t, value, e, nil, v.Expected.Error)
		})
	}
	for _, v := range corpus.EncodeErrors {
		t.Run("encode/"+v.Case, func(t *testing.T) {
			n, e := strconv.ParseUint(v.EncodeMaxBytes, 10, 64)
			if e != nil {
				t.Fatal(e)
			}
			_, e = s.EncodeValue(valueCodecFor(t, v.Schema), v.Value, n)
			compareValueResult(t, nil, e, nil, v.Expected.Error)
		})
	}
	for _, v := range corpus.Readers {
		t.Run("reader/"+v.Case, func(t *testing.T) { runValueReader(t, v) })
	}
	for _, v := range corpus.Lengths {
		t.Run("length/"+v.Case, func(t *testing.T) { runValueLength(t, v) })
	}
	for _, v := range corpus.Encoders {
		t.Run("encoder/"+v.Case, func(t *testing.T) { runValueEncoder(t, v) })
	}
	for _, v := range corpus.Native {
		t.Run("native/"+v.Case, func(t *testing.T) { runValueAdmission(t, v) })
	}
}
func valueCompositeFor(t *testing.T, shape valueSchema) s.ValueCodec[any] {
	t.Helper()
	must := func(c s.ValueCodec[any], e error) s.ValueCodec[any] {
		if e != nil {
			t.Fatal(e)
		}
		return c
	}
	layout := func(c s.ValueCodec[any], size uint64) s.ValueCodec[any] {
		l, e := s.NewValueLayout(size)
		if e != nil {
			t.Fatal(e)
		}
		return must(c.WithLayout(l))
	}
	switch shape.Kind {
	case "option":
		element := valueCodecFor(t, *shape.Element)
		typed, e := s.OptionOf(element)
		if e != nil {
			t.Fatal(e)
		}
		codec := erasedValueCodec(t, typed, func(v any) s.Option[any] {
			m := v.(map[string]any)
			if m["some"].(bool) {
				return s.Option[any]{Present: true, Value: m["value"]}
			}
			return s.Option[any]{}
		}, func(v s.Option[any]) any {
			if v.Present {
				return map[string]any{"some": true, "value": v.Value}
			}
			return map[string]any{"some": false}
		})
		sizes := map[string]uint64{"unit": 1, "bool": 1, "u8": 2, "u64": 16, "string": 24}
		if size, ok := sizes[shape.Element.Kind]; ok {
			return layout(codec, size)
		}
		return codec
	case "vector":
		element := valueCodecFor(t, *shape.Element)
		width := uint8(4)
		if shape.LengthWidth != nil {
			width = *shape.LengthWidth
		}
		typed, e := s.VectorLengthOf(element, width)
		if e != nil {
			t.Fatal(e)
		}
		return erasedValueCodec(t, typed, func(v any) []any { return v.([]any) }, func(v []any) any { return v })
	case "array":
		typed, e := s.ArrayOf(shape.Length, valueCodecFor(t, *shape.Element))
		if e != nil {
			t.Fatal(e)
		}
		codec := erasedValueCodec(t, typed, func(v any) []any { return v.([]any) }, func(v []any) any { return v })
		if shape.Element.Kind == "u16" && shape.Length == 3 {
			return layout(codec, 6)
		}
		return codec
	case "tuple":
		fields := make([]s.ValueCodec[any], len(shape.Fields))
		for i, v := range shape.Fields {
			fields[i] = valueCodecFor(t, v)
		}
		switch len(fields) {
		case 1:
			typed, e := s.Tuple1Of(fields[0])
			if e != nil {
				t.Fatal(e)
			}
			return erasedValueCodec(t, typed, func(v any) s.Tuple1[any] { return s.Tuple1[any]{V0: v.([]any)[0]} }, func(v s.Tuple1[any]) any { return []any{v.V0} })
		case 2:
			typed, e := s.Tuple2Of(fields[0], fields[1])
			if e != nil {
				t.Fatal(e)
			}
			return erasedValueCodec(t, typed, func(v any) s.Tuple2[any, any] { a := v.([]any); return s.Tuple2[any, any]{V0: a[0], V1: a[1]} }, func(v s.Tuple2[any, any]) any { return []any{v.V0, v.V1} })
		case 3:
			typed, e := s.Tuple3Of(fields[0], fields[1], fields[2])
			if e != nil {
				t.Fatal(e)
			}
			return erasedValueCodec(t, typed, func(v any) s.Tuple3[any, any, any] {
				a := v.([]any)
				return s.Tuple3[any, any, any]{V0: a[0], V1: a[1], V2: a[2]}
			}, func(v s.Tuple3[any, any, any]) any { return []any{v.V0, v.V1, v.V2} })
		case 4:
			typed, e := s.Tuple4Of(fields[0], fields[1], fields[2], fields[3])
			if e != nil {
				t.Fatal(e)
			}
			return erasedValueCodec(t, typed, func(v any) s.Tuple4[any, any, any, any] {
				a := v.([]any)
				return s.Tuple4[any, any, any, any]{V0: a[0], V1: a[1], V2: a[2], V3: a[3]}
			}, func(v s.Tuple4[any, any, any, any]) any { return []any{v.V0, v.V1, v.V2, v.V3} })
		}
	case "oracle_state":
		typed, e := s.OracleStateOf(valueCodecFor(t, *shape.Model), valueCodecFor(t, *shape.Oracle))
		if e != nil {
			t.Fatal(e)
		}
		return erasedValueCodec(t, typed, func(v any) s.OracleState[any, any] {
			a := v.(map[string]any)
			return s.OracleState[any, any]{Model: a["model"], Oracle: a["oracle"]}
		}, func(v s.OracleState[any, any]) any { return map[string]any{"model": v.Model, "oracle": v.Oracle} })
	}
	t.Fatal("unsupported schema", shape.Kind)
	return s.ValueCodec[any]{}
}
func valueDecodeLength(t *testing.T, d *s.Decoder, shape valueSchema, widthOverride *uint8) (any, error) {
	t.Helper()
	width := uint8(4)
	if shape.LengthWidth != nil {
		width = *shape.LengthWidth
	}
	if widthOverride != nil {
		width = *widthOverride
	}
	switch shape.Kind {
	case "string":
		v, e := s.DecodeStringLength(d, width)
		if e != nil {
			return nil, e
		}
		return v, nil
	case "vector":
		v, e := s.DecodeVectorLength(d, valueCodecFor(t, *shape.Element), width)
		if e != nil {
			return nil, e
		}
		return v, nil
	}
	return nil, errors.New("invalid length schema")
}

type valueReaderAction struct {
	Kind        string
	Count       string
	ElementSize string `json:"element_size"`
	Schema      valueSchema
	Width       *uint8
	Body        []valueReaderAction
	Returns     map[string]any
	OnError     string `json:"on_error"`
}
type valueReaderCase struct {
	Case, Scope string
	BytesHex    string `json:"bytes_hex"`
	Limits      map[string]string
	Actions     []valueReaderAction
	Expected    struct {
		ConstructionError *string `json:"construction_error"`
		Results           []map[string]any
		CallbackEvents    []string `json:"callback_events"`
	}
}

func runValueReader(t *testing.T, v valueReaderCase) {
	t.Helper()
	wire, e := hex.DecodeString(v.BytesHex)
	if e != nil {
		t.Fatal(e)
	}
	d, e := s.NewDecoder(wire, valueLimits(t, v.Limits))
	if v.Expected.ConstructionError != nil {
		if e == nil || e.Error() != *v.Expected.ConstructionError {
			t.Fatalf("constructor %v", e)
		}
		return
	}
	if e != nil {
		t.Fatal(e)
	}
	events := []string{}
	var action func(valueReaderAction) (any, error)
	parse := func(text string) uint64 {
		n, e := strconv.ParseUint(text, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		return n
	}
	action = func(a valueReaderAction) (any, error) {
		switch a.Kind {
		case "take":
			data, e := d.Take(parse(a.Count))
			if e != nil {
				return nil, e
			}
			return hex.EncodeToString(data), nil
		case "collection":
			return nil, d.Collection(parse(a.Count), parse(a.ElementSize))
		case "finish":
			return nil, d.Finish()
		case "value":
			return s.ReadValue(d, valueCodecFor(t, a.Schema))
		case "direct_decode":
			return valueCodecFor(t, a.Schema).DecodeRaw(d)
		case "direct_length":
			return valueDecodeLength(t, d, a.Schema, a.Width)
		case "value_custom":
			codec := mustValueCodec(t, "custom-reader", func(any, *s.EncodeBuffer) error { return nil }, func(*s.Decoder) (any, error) {
				events = append(events, "enter")
				for _, op := range a.Body {
					_, e := action(op)
					if e == nil {
						events = append(events, op.Kind+":ok")
						continue
					}
					events = append(events, op.Kind+":error:"+e.Error())
					if op.OnError == "swallow" {
						events = append(events, "swallow")
						continue
					}
					events = append(events, "propagate:"+e.Error())
					return nil, e
				}
				if message, ok := a.Returns["error"]; ok {
					events = append(events, "return_error:"+message.(string))
					return nil, errors.New(message.(string))
				}
				events = append(events, "return_ok")
				return a.Returns["ok"], nil
			}, nil)
			return s.ReadValue(d, codec)
		}
		return nil, fmt.Errorf("unknown reader action %s", a.Kind)
	}
	results := []map[string]any{}
	for _, a := range v.Actions {
		value, e := action(a)
		var message any
		if e != nil {
			message = e.Error()
			value = nil
		}
		results = append(results, map[string]any{"value": value, "error": message})
	}
	if !reflect.DeepEqual(results, v.Expected.Results) {
		a, _ := json.Marshal(results)
		b, _ := json.Marshal(v.Expected.Results)
		t.Fatalf("results=%s expected=%s", a, b)
	}
	if !reflect.DeepEqual(events, v.Expected.CallbackEvents) {
		t.Fatalf("events=%v expected=%v", events, v.Expected.CallbackEvents)
	}
}

type valueLengthCase struct {
	Case, Operation, Scope string
	Width                  uint8
	Count                  string
	Schema                 valueSchema
	Expected               struct {
		BytesHex *string `json:"bytes_hex"`
		Error    *string
	}
}

func runValueLength(t *testing.T, v valueLengthCase) {
	t.Helper()
	if v.Operation == "write_length_primitive" {
		t.Log("Arithmetic-only fixture witness; no exported Rust/public codec execution claim")
		n, e := strconv.ParseUint(v.Count, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		maximum := uint64(1)<<(8*v.Width) - 1
		if n > maximum {
			if v.Expected.Error == nil || *v.Expected.Error != "length overflow" {
				t.Fatal("invalid arithmetic overflow witness")
			}
			return
		}
		out := make([]byte, v.Width)
		for i := range out {
			out[i] = byte(n >> (8 * i))
		}
		if v.Expected.BytesHex == nil || hex.EncodeToString(out) != *v.Expected.BytesHex {
			t.Fatal("invalid arithmetic prefix witness")
		}
		return
	}
	var out []byte
	var rawError error
	e := s.WithEncodeBuffer(&out, 1024, func(b *s.EncodeBuffer) error { rawError = s.EncodeStringLength("", v.Width, b); return nil })
	if e != nil {
		t.Fatal(e)
	}
	compareValueResult(t, nil, rawError, nil, v.Expected.Error)
}

type valueAdmissionCase struct {
	Case          string
	Schema        valueSchema
	Value         any
	ExpectedError string          `json:"expected_error"`
	UTF16         []uint16        `json:"utf16_code_units"`
	HostValue     json.RawMessage `json:"host_value"`
}

func runValueAdmission(t *testing.T, v valueAdmissionCase) {
	t.Helper()
	if v.Schema.Kind == "array" {
		_, e := s.EncodeValue(valueCodecFor(t, v.Schema), v.Value, 1024)
		if e == nil || !strings.Contains(e.Error(), "array") {
			t.Fatalf("wrong-length array accepted/error=%v", e)
		}
		return
	}
	if v.Case == "lone-surrogate-string" {
		t.Skip("Go strings have no UTF-16 code-unit value; malformed UTF-8 admission is tested separately at public StringCodec")
	}
	t.Skip("Static Go type exclusion: typed exact-width integer/bool codecs cannot accept the fixture's out-of-range or wrong host type; no narrowing cast was executed")
}
func TestCanonicalValueNativeInvalidUTF8(t *testing.T) {
	for _, v := range []string{"\xff", "\xed\xa0\x80"} {
		if _, e := s.EncodeValue(s.StringCodec(), v, 1024); e == nil || !strings.Contains(e.Error(), "UTF-8") {
			t.Fatalf("invalid Go string admitted: %v", e)
		}
	}
}

func TestCanonicalValueNativeReaderOwnershipAndPanic(t *testing.T) {
	input := []byte{7}
	d, e := s.NewDecoder(input, s.DefaultDecodeLimits())
	if e != nil {
		t.Fatal(e)
	}
	input[0] = 99
	v, e := s.ReadValue(d, s.Uint8Codec())
	if e != nil || v != 7 {
		t.Fatalf("reader aliased caller %d %v", v, e)
	}
	d, e = s.NewDecoder(nil, s.DefaultDecodeLimits())
	if e != nil {
		t.Fatal(e)
	}
	codec := mustValueCodec(t, "panic-after-failure", func(s.Unit, *s.EncodeBuffer) error { return nil }, func(d *s.Decoder) (s.Unit, error) { d.Take(1); panic("later callback panic") }, nil)
	_, e = s.ReadValue(d, codec)
	if e == nil || e.Error() != "truncated value" || d.Depth() != 0 {
		t.Fatalf("firstfailure/depth %v %d", e, d.Depth())
	}
	if e = d.Finish(); e == nil || e.Error() != "truncated value" {
		t.Fatal("panic overwrote first failure")
	}
	d, e = s.NewDecoder(nil, s.DefaultDecodeLimits())
	if e != nil {
		t.Fatal(e)
	}
	codec = mustValueCodec(t, "panic", func(s.Unit, *s.EncodeBuffer) error { return nil }, func(*s.Decoder) (s.Unit, error) { panic("callback boom") }, nil)
	if _, e = s.ReadValue(d, codec); e == nil || d.Depth() != 0 {
		t.Fatalf("panic not contained/depth restored %v", e)
	}
	if _, e = d.Take(0); e == nil {
		t.Fatal("panic not sticky")
	}
}
func TestCanonicalValueNativeEncodeLeaseAndIgnoredFailure(t *testing.T) {
	var retained *s.EncodeBuffer
	codec := mustValueCodec(t, "lease", func(v uint8, b *s.EncodeBuffer) error { retained = b; return b.Append([]byte{v}) }, func(d *s.Decoder) (uint8, error) { return s.Uint8Codec().DecodeRaw(d) }, nil)
	data, e := s.EncodeValue(codec, uint8(7), 1)
	if e != nil || !bytes.Equal(data, []byte{7}) {
		t.Fatal(e)
	}
	if e = retained.Append([]byte{8}); e == nil {
		t.Fatal("escaped encode lease active")
	}
	if !bytes.Equal(data, []byte{7}) {
		t.Fatal("expired lease mutated result")
	}
	ignored := mustValueCodec(t, "ignored-limit", func(_ s.Unit, b *s.EncodeBuffer) error { b.Append([]byte{1, 2}); return nil }, func(*s.Decoder) (s.Unit, error) { return s.Unit{}, nil }, nil)
	if _, e = s.EncodeValue(ignored, s.Unit{}, 1); e == nil {
		t.Fatal("ignored encode error bypassed sticky finish")
	}
}
func TestCanonicalValueNativeLayoutIdentityCannotBeSpoofed(t *testing.T) {
	calls := 0
	forged := mustValueCodec(t, "u64", func(v uint64, b *s.EncodeBuffer) error { return s.Uint64Codec().EncodeInto(v, b) }, func(d *s.Decoder) (uint64, error) { calls++; return s.Uint64Codec().DecodeRaw(d) }, nil)
	vector, e := s.VectorOf(forged)
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.FromValueBytes(vector, []byte{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, s.DefaultDecodeLimits())
	if e == nil || calls != 0 {
		t.Fatal("diagnostic schema fabricated collection layout")
	}
	layout, e := s.NewValueLayout(8)
	if e != nil {
		t.Fatal(e)
	}
	explicit, e := forged.WithLayout(layout)
	if e != nil {
		t.Fatal(e)
	}
	vector, e = s.VectorOf(explicit)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.FromValueBytes(vector, []byte{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, s.DefaultDecodeLimits()); e != nil || calls != 1 {
		t.Fatalf("explicit layout rejected %v calls%d", e, calls)
	}
	if _, e = forged.WithLayout(s.ValueLayout{}); e == nil {
		t.Fatal("default layout accepted")
	}
}

type valueEncoderCase struct {
	Case     string
	MaxBytes string `json:"max_bytes"`
	Actions  []struct {
		Kind              string
		Schema            valueSchema
		Width             uint8
		Value             any
		BytesHex          string  `json:"bytes_hex"`
		PrefixHex         string  `json:"prefix_hex"`
		ReturnsError      *string `json:"returns_error"`
		IgnorePrefixError bool    `json:"ignore_prefix_error"`
	}
	Expected struct {
		Results  []map[string]any
		BytesHex string `json:"bytes_hex"`
	}
}

func runValueEncoder(t *testing.T, v valueEncoderCase) {
	t.Helper()
	maximum, e := strconv.ParseUint(v.MaxBytes, 10, 64)
	if e != nil {
		t.Fatal(e)
	}
	results := []map[string]any{}
	var encoded []byte
	s.WithEncodeBuffer(&encoded, maximum, func(buffer *s.EncodeBuffer) error {
		for _, a := range v.Actions {
			var e error
			switch a.Kind {
			case "direct_length":
				e = s.EncodeLengthRaw(a.Value.(string), a.Width, buffer)
			case "append":
				data, error := hex.DecodeString(a.BytesHex)
				if error != nil {
					t.Fatal(error)
				}
				e = buffer.Append(data)
			case "finish":
				e = s.FinishValueEncoding(buffer)
			case "direct_encode":
				e = valueCodecFor(t, a.Schema).EncodeRaw(a.Value, buffer)
			case "direct_encode_custom":
				prefix, prefixError := hex.DecodeString(a.PrefixHex)
				if prefixError != nil {
					t.Fatal(prefixError)
				}
				codec := mustValueCodec(t, "raw-custom-encode", func(_ s.Unit, b *s.EncodeBuffer) error {
					if e := b.Append(prefix); e != nil && !a.IgnorePrefixError {
						return e
					}
					if a.ReturnsError != nil {
						return errors.New(*a.ReturnsError)
					}
					return nil
				}, func(*s.Decoder) (s.Unit, error) { return s.Unit{}, nil }, nil)
				e = codec.EncodeRaw(s.Unit{}, buffer)
			default:
				t.Fatal("unknown encoder action", a.Kind)
			}
			var message any
			if e != nil {
				message = e.Error()
			}
			var family any
			if e != nil {
				switch {
				case strings.Contains(e.Error(), "unsupported length width"):
					family = "unsupported_length_width"
				case strings.Contains(e.Error(), "length overflow"):
					family = "length_overflow"
				case strings.Contains(e.Error(), "encoded blob exceeds"):
					family = "encode_limit"
				default:
					family = "custom_callback"
				}
			}
			results = append(results, map[string]any{"value": nil, "error": message, "error_family": family})
		}
		return nil
	})
	if !reflect.DeepEqual(results, v.Expected.Results) {
		a, _ := json.Marshal(results)
		b, _ := json.Marshal(v.Expected.Results)
		t.Fatalf("results=%s want=%s", a, b)
	}
	if hex.EncodeToString(encoded) != v.Expected.BytesHex {
		t.Fatalf("bytes=%x want=%s", encoded, v.Expected.BytesHex)
	}
}

type hostileValuePanic struct{ calls *int }

func (p hostileValuePanic) String() string { *p.calls++; panic("panic payload formatter called") }
func TestCanonicalValueNativeVariantDiagnostics(t *testing.T) {
	codec := s.BoolCodec().WithVariantFunc(func(v bool) (string, bool) {
		if v {
			return "true-case", true
		}
		return "false-case", true
	})
	if label, present, e := codec.VariantFor(true); e != nil || !present || label != "true-case" {
		t.Fatalf("%q %t %v", label, present, e)
	}
	if label, present, e := codec.VariantFor(false); e != nil || !present || label != "false-case" {
		t.Fatalf("%q %t %v", label, present, e)
	}
	if label, present, e := s.Uint8Codec().WithVariant("stable").VariantFor(7); e != nil || !present || label != "stable" {
		t.Fatal("static label lost")
	}
	calls := 0
	hostile := hostileValuePanic{&calls}
	codec = codec.WithVariantFunc(func(bool) (string, bool) { panic(hostile) })
	if _, _, e := codec.VariantFor(true); e == nil || calls != 0 {
		t.Fatalf("variant panic formatted payload: %v count%d", e, calls)
	}
	encoder := mustValueCodec(t, "hostile-encoder", func(s.Unit, *s.EncodeBuffer) error { panic(hostile) }, func(*s.Decoder) (s.Unit, error) { panic(hostile) }, nil)
	if _, e := s.EncodeValue(encoder, s.Unit{}, 1); e == nil || calls != 0 {
		t.Fatal("encode panic stringified payload")
	}
	if _, e := s.FromValueBytes(encoder, nil, s.DefaultDecodeLimits()); e == nil || calls != 0 {
		t.Fatal("decode panic stringified payload")
	}
}

func TestCanonicalValueNativeRawBoundaryRetainsCooperativeErrors(t *testing.T) {
	var out []byte
	later := errors.New("later returned error")
	s.WithEncodeBuffer(&out, 0, func(b *s.EncodeBuffer) error {
		if e := b.Append([]byte{1}); e == nil {
			t.Fatal("write unexpectedly fit")
		}
		if e := s.UnitCodec().EncodeRaw(s.Unit{}, b); e != nil {
			t.Fatalf("raw unit forced prior write error: %v", e)
		}
		raw := mustValueCodec(t, "later-return", func(s.Unit, *s.EncodeBuffer) error { return later }, func(*s.Decoder) (s.Unit, error) { return s.Unit{}, nil }, nil)
		if e := raw.EncodeRaw(s.Unit{}, b); e != later {
			t.Fatalf("prior failure replaced raw returned error %v", e)
		}
		if e := s.FinishValueEncoding(b); e == nil || e.Error() != "encoded blob exceeds byte or allocation limit" {
			t.Fatalf("raw calls erased sticky write failure: %v", e)
		}
		return nil
	})
	out = nil
	e := s.WithEncodeBuffer(&out, 1, func(b *s.EncodeBuffer) error {
		bad, e := s.StringLengthCodec(3)
		if e != nil {
			t.Fatal(e)
		}
		outer := mustValueCodec(t, "recover-validation", func(_ s.Unit, child *s.EncodeBuffer) error {
			if e := bad.EncodeRaw("", child); e == nil {
				t.Fatal("unsupported width succeeded")
			}
			return child.Append([]byte{0xaa})
		}, func(*s.Decoder) (s.Unit, error) { return s.Unit{}, nil }, nil)
		if e = outer.EncodeRaw(s.Unit{}, b); e != nil {
			return e
		}
		return s.FinishValueEncoding(b)
	})
	if e != nil || !bytes.Equal(out, []byte{0xaa}) {
		t.Fatalf("caught nested validation poisoned raw sink: %x %v", out, e)
	}
}
