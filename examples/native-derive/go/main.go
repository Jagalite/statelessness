package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
)

type Point struct {
	X uint32
	Y string
}
type Event struct {
	Tag   uint32
	Point Point
}
type Row struct {
	ID  string `json:"id"`
	Hex string `json:"hex"`
}

func must[T any](v T, e error) T {
	if e != nil {
		panic(e)
	}
	return v
}
func main() {
	x := must(s.OrdinaryField("x", s.Uint32Codec()))
	y := must(s.OrdinaryField("y", s.StringCodec()))
	point := must(s.OrderedRecord("Point", s.PrependRecordField(x, s.PrependRecordField(y, s.NoRecordFields())), func(v Point) (s.FieldProduct[uint32, s.FieldProduct[string, s.Unit]], error) {
		return s.FieldProduct[uint32, s.FieldProduct[string, s.Unit]]{Head: v.X, Tail: s.FieldProduct[string, s.Unit]{Head: v.Y}}, nil
	}, func(v s.FieldProduct[uint32, s.FieldProduct[string, s.Unit]]) (Point, error) {
		return Point{v.Head, v.Tail.Head}, nil
	}, s.RecordOptions{}))
	empty := must(s.TaggedCase(0, "Empty", s.UnitCodec(), func(v Event) (s.Unit, error) {
		if v.Tag != 0 {
			return s.Unit{}, fmt.Errorf("case mismatch")
		}
		return s.Unit{}, nil
	}, func(s.Unit) (Event, error) { return Event{Tag: 0}, nil }))
	full := must(s.TaggedCase(1, "Point", point, func(v Event) (Point, error) {
		if v.Tag != 1 {
			return Point{}, fmt.Errorf("case mismatch")
		}
		return v.Point, nil
	}, func(p Point) (Event, error) { return Event{Tag: 1, Point: p}, nil }))
	event := must(s.TaggedEnum("Event", func(v Event) (uint32, error) { return v.Tag, nil }, s.DefaultEnumOptions(), empty, full))
	rows := []Row{{"point", "07000000020000006869"}, {"empty", "00"}, {"variant", "0107000000020000006869"}}
	if len(os.Args) < 3 {
		panic("produce DEST or transcode SOURCE DEST")
	}
	dest := os.Args[2]
	if os.Args[1] == "transcode" {
		raw := must(os.ReadFile(os.Args[2]))
		if e := json.Unmarshal(raw, &rows); e != nil {
			panic(e)
		}
		dest = os.Args[3]
	}
	if len(rows) != 3 {
		panic("case count")
	}
	for i, row := range rows {
		data := must(hex.DecodeString(row.Hex))
		var out []byte
		switch row.ID {
		case "point":
			v := must(s.FromValueBytes(point, data, s.DefaultDecodeLimits()))
			if v != (Point{7, "hi"}) {
				panic("point")
			}
			out = must(s.EncodeValue(point, v, 100))
		case "empty", "variant":
			v := must(s.FromValueBytes(event, data, s.DefaultDecodeLimits()))
			if row.ID == "empty" && v.Tag != 0 {
				panic("empty")
			}
			if row.ID == "variant" && (v.Tag != 1 || v.Point != (Point{7, "hi"})) {
				panic("variant")
			}
			out = must(s.EncodeValue(event, v, 100))
		default:
			panic("unknown row")
		}
		rows[i].Hex = hex.EncodeToString(out)
	}
	raw := must(json.Marshal(rows))
	if e := os.WriteFile(dest, raw, 0600); e != nil {
		panic(e)
	}
	fmt.Println(`{"case_count":3,"output_encode_only":false}`)
}
