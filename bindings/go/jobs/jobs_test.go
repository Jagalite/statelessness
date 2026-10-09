package jobs

import "testing"

func TestStaleCompletion(t *testing.T) {
	for _, bad := range []bool{false, true} {
		m := Model{Faulty: bad}
		s, _ := m.Initial()
		for _, e := range [][]byte{{0, 0}, {1, 0}} {
			r, err := m.Step(s, e)
			if err != nil {
				t.Fatal(err)
			}
			s = r.State
		}
		r, err := m.Step(s, []byte{4, 0})
		if err != nil {
			t.Fatal(err)
		}
		if (r.Disposition == 0) != bad {
			t.Fatal(r)
		}
	}
}
func TestMalformed(t *testing.T) {
	m := Model{}
	for _, b := range [][]byte{nil, {0}, {5, 1}, {1, 3}, {0, 1}} {
		if _, e := m.CheckState(b); e == nil {
			t.Fatalf("state %v", b)
		}
	}
	for _, e := range [][]byte{nil, {0}, {5, 0}, {3, 3}, {0, 0, 0}} {
		if _, err := m.Step([]byte{0, 0}, e); err == nil {
			t.Fatal(e)
		}
	}
}

func TestDeclaredDomain(t *testing.T) {
	m := Model{}
	for _, state := range [][]byte{{0, 0}, {1, 1}, {2, 1}, {3, 1}, {4, 1}, {1, 2}, {2, 2}, {3, 2}, {4, 2}} {
		inputs, err := m.Inputs(state)
		if err != nil {
			t.Fatal(err)
		}
		if len(inputs) != 15 {
			t.Fatalf("domain omitted events at %v", state)
		}
		for index, event := range inputs {
			if event[0] != byte(index/3) || event[1] != byte(index%3) {
				t.Fatalf("unstable enumeration %v", inputs)
			}
		}
	}
}
