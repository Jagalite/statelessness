/// SplitMix64 with explicit unsigned wrapping arithmetic. Not cryptographic.
/// Each independent job must own its own generator state.
public struct Rng {
    private var state: UInt64
    public init(seed: UInt64 = 0) { state = seed }
    public mutating func nextU64() -> UInt64 {
        state = state &+ 0x9e3779b97f4a7c15
        var z = state
        z = (z ^ (z >> 30)) &* 0xbf58476d1ce4e5b9
        z = (z ^ (z >> 27)) &* 0x94d049bb133111eb
        return z ^ (z >> 31)
    }
    /// An empty domain returns nil without consuming generator state.
    public mutating func index(_ upper: UInt64) -> UInt64? {
        guard upper > 0 else { return nil }
        let threshold = (UInt64(0) &- upper) % upper
        while true { let value = nextU64(); if value >= threshold { return value % upper } }
    }
}
