package portablejson

import "testing"

func TestRejectMalformed(t *testing.T) {
	for _, raw := range []string{`{"x":1,"x":2}`, `{"x":"\ud800"}`, `{"x":"\udc00"}`, `{"x":1.0}`, `{"x":1e0}`, `{} garbage`, "\xef\xbb\xbf{}", `{"x":"\ud800\ud800"}`, "\xff"} {
		if _, err := Strict([]byte(raw), 1024); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}
func TestSurrogatesAndLiteralBackslash(t *testing.T) {
	for _, raw := range []string{`"\ud83d\ude00"`, `"\\ud800"`, `{"é":1,"e\u0301":2}`} {
		if _, err := Strict([]byte(raw), 1024); err != nil {
			t.Fatal(raw, err)
		}
	}
}
func TestBooleanNotInteger(t *testing.T) {
	r := &Reader{}
	r.Integer(true, 10)
	if r.Err == nil {
		t.Fatal("boolean accepted as number")
	}
}
