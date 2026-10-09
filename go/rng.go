package statelessness

// Rng is SplitMix64 with explicit unsigned wraparound. It is not cryptographic.
// Independent jobs need separate instances; mutation is not concurrency-safe.
type Rng struct{ state uint64 }

func NewRng(seed uint64) *Rng { return &Rng{state: seed} }
func (r *Rng) NextU64() uint64 {
	r.state += 0x9e3779b97f4a7c15
	z := r.state
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
	z = (z ^ (z >> 27)) * 0x94d049bb133111eb
	return z ^ (z >> 31)
}

// Index returns (0,false) without consuming a draw for an empty domain.
func (r *Rng) Index(upper uint64) (uint64, bool) {
	if upper == 0 {
		return 0, false
	}
	threshold := (uint64(0) - upper) % upper
	for {
		value := r.NextU64()
		if value >= threshold {
			return value % upper, true
		}
	}
}
