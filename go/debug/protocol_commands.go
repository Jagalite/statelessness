package debug

import (
	"encoding/hex"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// Decode command syntax before admission, as the Rust protocol's typed Command
// decoder does. Canonical arguments also give typed equality to deduplication:
// decimal padding and hex letter case do not change the requested operation.
func commandArguments(command string, args []string, limits ProtocolLimits) ([]string, error) {
	args = append([]string{}, args...)
	malformed := ProtocolError("malformed")
	switch command {
	case "status", "snapshot", "cancel":
		if len(args) != 0 {
			return nil, malformed
		}
		return args, nil
	case "step":
		if len(args) != 1 || !strings.HasPrefix(args[0], "bytes:") || (len(args[0])-6)%2 != 0 {
			return nil, malformed
		}
		if (len(args[0])-6)/2 > limits.MaxInput {
			return nil, ProtocolError("limit")
		}
		bytes, err := hex.DecodeString(args[0][6:])
		if err != nil {
			return nil, malformed
		}
		args[0] = "bytes:" + hex.EncodeToString(bytes)
		return args, nil
	}
	shape, known := adapterShape(command)
	if !known {
		return nil, ProtocolError("unsupported")
	}
	if len(args) < len(shape.tags) || (!shape.path && command != "probe_configure" && len(args) != len(shape.tags)) {
		return nil, malformed
	}
	if shape.path && len(args)-len(shape.tags) > 32 {
		return nil, ProtocolError("limit")
	}
	for i, tag := range shape.tags {
		switch tag {
		case "u64:", "handle":
			if tag == "handle" && args[i] == "none" {
				continue
			}
			n, err := parseNumber(args[i])
			if err != nil {
				return nil, err
			}
			if (command == "inspect" && i == 1 || command == "watch" && i == 0) && n > math.MaxUint32 {
				return nil, malformed
			}
			args[i] = number(n)
		case "bool:":
			if args[i] != "bool:true" && args[i] != "bool:false" {
				return nil, malformed
			}
		}
	}
	if command == "probe_configure" {
		for i := 1; i < len(args); i++ {
			n, err := parseNumber(args[i])
			if err != nil {
				return nil, err
			}
			args[i] = number(n)
		}
	}
	if shape.path {
		for i := len(shape.tags); i < len(args); i++ {
			segment, err := canonicalSegment(args[i])
			if err != nil {
				return nil, err
			}
			args[i] = segment
		}
	}
	return args, nil
}

func canonicalSegment(segment string) (string, error) {
	kind, value, ok := strings.Cut(segment, ":")
	if !ok {
		return "", ProtocolError("malformed")
	}
	switch kind {
	case "field", "variant", "key.string":
		return segment, nil
	case "key.bool":
		if value == "true" || value == "false" {
			return segment, nil
		}
	case "key.char":
		if len([]rune(value)) == 1 {
			return segment, nil
		}
	case "index":
		n, err := strconv.ParseUint(strings.TrimPrefix(value, "+"), 10, strconv.IntSize)
		if err == nil {
			return "index:" + strconv.FormatUint(n, 10), nil
		}
	case "key.u8", "key.u16", "key.u32", "key.u64", "key.u128", "key.i8", "key.i16", "key.i32", "key.i64", "key.i128", "key.usize", "key.isize":
		checkKind := kind
		if strings.HasSuffix(kind, "size") {
			checkKind = kind[:5] + strconv.Itoa(strconv.IntSize)
		}
		// Rust's integer parser allows a leading plus, but no other syntax.
		digits := strings.TrimPrefix(value, "+")
		if strings.HasPrefix(digits, "-") && kind[4] == 'i' {
			digits = digits[1:]
		} else if strings.HasPrefix(value, "-") {
			return "", ProtocolError("malformed")
		}
		if digits == "" {
			return "", ProtocolError("malformed")
		}
		for _, c := range digits {
			if c < '0' || c > '9' {
				return "", ProtocolError("malformed")
			}
		}
		if validIntegerKey(checkKind, value) {
			n, _ := new(big.Int).SetString(value, 10)
			return kind + ":" + n.String(), nil
		}
	}
	return "", ProtocolError("malformed")
}
