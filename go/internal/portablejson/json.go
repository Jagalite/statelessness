// Package portablejson owns the deliberately strict JSON boundary, not an engine.
package portablejson

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf8"
)

type Error struct{ Message string }

func (e *Error) Error() string { return e.Message }
func bad(message string) error { return &Error{message} }

// Strict rejects duplicate keys, lossy Unicode replacement, floats, and trailing
// data. encoding/json alone accepts several of those cases, so it is insufficient.
func Strict(raw []byte, maximum int) (any, error) {
	if len(raw) > maximum || !utf8.Valid(raw) {
		return nil, bad("JSON byte limit or invalid UTF-8")
	}
	if err := unicodeEscapes(raw); err != nil {
		return nil, err
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > 64 {
			return nil, bad("JSON nesting limit")
		}
		token, err := d.Token()
		if err != nil {
			return nil, bad(err.Error())
		}
		switch v := token.(type) {
		case json.Delim:
			switch v {
			case '{':
				result := map[string]any{}
				for d.More() {
					t, e := d.Token()
					if e != nil {
						return nil, bad(e.Error())
					}
					key, ok := t.(string)
					if !ok {
						return nil, bad("invalid object key")
					}
					if _, ok = result[key]; ok {
						return nil, bad("duplicate object key")
					}
					result[key], e = read(depth + 1)
					if e != nil {
						return nil, e
					}
				}
				t, e := d.Token()
				if e != nil || t != json.Delim('}') {
					return nil, bad("unclosed object")
				}
				return result, nil
			case '[':
				result := []any{}
				for d.More() {
					value, e := read(depth + 1)
					if e != nil {
						return nil, e
					}
					result = append(result, value)
				}
				t, e := d.Token()
				if e != nil || t != json.Delim(']') {
					return nil, bad("unclosed array")
				}
				return result, nil
			default:
				return nil, bad("unexpected delimiter")
			}
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				return nil, bad("floating numbers are outside the profile")
			}
			if strings.HasPrefix(string(v), "-") {
				if _, e := strconv.ParseInt(string(v), 10, 64); e != nil {
					return nil, bad("integer overflow")
				}
			} else {
				if _, e := strconv.ParseUint(string(v), 10, 64); e != nil {
					return nil, bad("integer overflow")
				}
			}
			return v, nil
		case string, bool, nil:
			return token, nil
		default:
			return nil, bad("invalid JSON token")
		}
	}
	result, err := read(0)
	if err != nil {
		return nil, err
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, bad("trailing JSON data")
	}
	return result, nil
}

// Reject unmatched UTF-16 surrogate escapes before Go's JSON decoder replaces
// them with U+FFFD. Escaped backslashes are consumed, so literal "\\ud800" is valid.
func unicodeEscapes(raw []byte) error {
	inside := false
	for i := 0; i < len(raw); i++ {
		if raw[i] == '"' {
			inside = !inside
			continue
		}
		if !inside || raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return bad("truncated escape")
		}
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return bad("truncated Unicode escape")
		}
		first, e := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if e != nil {
			return bad("invalid Unicode escape")
		}
		i += 4
		if first >= 0xdc00 && first <= 0xdfff {
			return bad("unpaired low surrogate")
		}
		if first >= 0xd800 && first <= 0xdbff {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return bad("unpaired high surrogate")
			}
			second, e := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if e != nil || second < 0xdc00 || second > 0xdfff {
				return bad("invalid surrogate pair")
			}
			i += 6
		}
	}
	return nil
}

// Reader accumulates the first structural error. Its empty fallback containers
// are safe to inspect; callers must check Err before executing any model work.
type Reader struct{ Err error }

func (r *Reader) Fail(message string) {
	if r.Err == nil {
		r.Err = bad(message)
	}
}
func (r *Reader) Object(v any, required []string, optional ...string) map[string]any {
	o, ok := v.(map[string]any)
	if !ok {
		r.Fail("expected object")
		return map[string]any{}
	}
	allowed := map[string]bool{}
	for _, k := range required {
		allowed[k] = true
		if _, ok := o[k]; !ok {
			r.Fail("missing field: " + k)
		}
	}
	for _, k := range optional {
		allowed[k] = true
	}
	for k := range o {
		if !allowed[k] {
			r.Fail("unknown field: " + k)
		}
	}
	return o
}
func (r *Reader) Text(v any) string {
	s, ok := v.(string)
	if !ok || !utf8.ValidString(s) {
		r.Fail("expected scalar string")
		return ""
	}
	return s
}
func (r *Reader) Integer(v any, max uint64) uint64 {
	n, ok := v.(json.Number)
	if !ok {
		r.Fail("expected integer")
		return 0
	}
	text := string(n)
	if text == "-0" {
		text = "0"
	}
	result, err := strconv.ParseUint(text, 10, 64)
	if err != nil || result > max {
		r.Fail("integer range")
		return 0
	}
	return result
}
func (r *Reader) Bool(v any) bool {
	b, ok := v.(bool)
	if !ok {
		r.Fail("expected boolean")
	}
	return b
}
func (r *Reader) Array(v any, max int) []any {
	a, ok := v.([]any)
	if !ok || len(a) > max {
		r.Fail("expected bounded array")
		return []any{}
	}
	return a
}
func (r *Reader) Strings(v any, max int) []string {
	a := r.Array(v, max)
	result := make([]string, len(a))
	for i, x := range a {
		result[i] = r.Text(x)
	}
	return result
}
func Default(o map[string]any, key string, value any) any {
	if x, ok := o[key]; ok {
		return x
	}
	return value
}
func (r *Reader) U64(v any) uint64 {
	s := r.Text(v)
	if len(s) == 0 || len(s) > 20 || (len(s) > 1 && s[0] == '0') {
		r.Fail("invalid decimal u64")
		return 0
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			r.Fail("invalid decimal u64")
			return 0
		}
	}
	n, err := strconv.ParseUint(s, 10, 64)
	if err != nil {
		r.Fail(fmt.Sprintf("invalid u64: %v", err))
	}
	return n
}
