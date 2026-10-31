extension LifecycleMonitor {
    /// Legacy bytes remain unversioned; indexed history begins with version 1.
    public func historyCodec(key:TraceValueCodec<K>) throws -> TraceValueCodec<LifecycleHistory<K>> {
        guard let layout = key.layout, layout.size <= UInt64.max-256 else { throw ModelError("explicit key allocation layout required") }; let indexed = epoch != nil
        return TraceValueCodec(schema:indexed ? "indexed-lifecycle-v1" : "legacy-lifecycle",encode:{ h,b in
            if indexed { try self.validate(h); try ValueCodecs.u8.encodeRaw(1,into:b) } else { try self.validateLegacyRepresentation(h) }; try ValueCodecs.u64.encodeRaw(h.now,into:b); if indexed { try ValueCodecs.u64.encodeRaw(h.retiredBefore,into:b) }
            func count(_ n:Int) throws { guard let n = UInt32(exactly:n) else { throw ModelError("lifecycle collection length") }; try ValueCodecs.u32.encodeRaw(n,into:b) }
            func obligation(_ o:LifecycleObligation,_ full:Bool) throws { try ValueCodecs.u64.encodeRaw(o.deadline,into:b); try ValueCodecs.bool.encodeRaw(o.cancelled,into:b); try ValueCodecs.bool.encodeRaw(o.settled,into:b); if full { try ValueCodecs.bool.encodeRaw(o.published,into:b) } }
            try count(h.obligations.count); for (k,v) in h.obligations { try key.encodeRaw(k,into:b); try obligation(v,indexed) }; try count(h.resources.count); for (k,v) in h.resources { try key.encodeRaw(k,into:b); try ValueCodecs.bool.encodeRaw(v,into:b) }; try count(h.violations.count)
            for v in h.violations {
                if !indexed { try ValueCodecs.string.encodeRaw(v.legacy ?? legacyLifecycleMessages[v.code] ?? v.code,into:b); continue }
                try ValueCodecs.u8.encodeRaw(v.key == nil ? 0 : 1,into:b); if let k = v.key { try key.encodeRaw(k,into:b) }; try ValueCodecs.string.encodeRaw(v.code,into:b); try ValueCodecs.string.encodeRaw(v.event,into:b); try ValueCodecs.u64.encodeRaw(v.observedAt,into:b); try ValueCodecs.u8.encodeRaw(v.prior == nil ? 0 : 1,into:b); if let p = v.prior { try obligation(p,true) }
            }
        },decode:{ r in
            if indexed { guard try r.value(ValueCodecs.u8) == 1 else { throw ModelError("indexed lifecycle codec version") } }; var h = LifecycleHistory<K>(); h.now = try r.value(ValueCodecs.u64); if indexed { h.retiredBefore = try r.value(ValueCodecs.u64) }
            func count(_ maximum: Int) throws -> Int {
                let n = try r.value(ValueCodecs.u32)
                guard maximum >= 0,UInt64(n) <= UInt64(maximum) else { throw ModelError("lifecycle retention limit; verification incomplete") }
                try r.collection(UInt64(n),elementSize:layout.size+256); return Int(n)
            }
            func obligation(_ full:Bool) throws -> LifecycleObligation { var o = LifecycleObligation(deadline:try r.value(ValueCodecs.u64)); o.cancelled = try r.value(ValueCodecs.bool); o.settled = try r.value(ValueCodecs.bool); if full { o.published = try r.value(ValueCodecs.bool) }; return o }
            func option<T>(_ read:() throws -> T) throws -> T? { let tag = try r.value(ValueCodecs.u8); guard tag <= 1 else { throw ModelError("invalid option tag") }; return tag == 1 ? try read() : nil }
            for _ in 0..<(try count(limits.obligations)) { h.obligations.append((try r.value(key),try obligation(indexed))) }; for _ in 0..<(try count(limits.resources)) { h.resources.append((try r.value(key),try r.value(ValueCodecs.bool))) }
            for _ in 0..<(try count(limits.violations)) {
                if !indexed { let text = try r.value(ValueCodecs.string); let code = legacyLifecycleMessages.first { exactText($0.value,text) }?.key ?? text; h.violations.append(LifecycleViolation(key:nil,code:code,event:"",observedAt:0,prior:nil,legacy:text)) }
                else { h.violations.append(LifecycleViolation(key:try option { try r.value(key) },code:try r.value(ValueCodecs.string),event:try r.value(ValueCodecs.string),observedAt:try r.value(ValueCodecs.u64),prior:try option { try obligation(true) })) }
            }; try self.validate(h); return h
        })
    }
}
public func scopedKeyCodec<K:Comparable>(_ key:TraceValueCodec<K>) throws -> TraceValueCodec<ScopedKey<K>> {
    guard let layout = key.layout, layout.size <= UInt64.max-16 else { throw ModelError("key layout required") }
    return TraceValueCodec(schema:"ScopedKey<\(key.schema)>",layout:Rust64ValueLayout(size:layout.size+16,identity:"scoped-key"),encode:{ k,b in try ValueCodecs.u64.encodeRaw(k.epoch,into:b); try ValueCodecs.u64.encodeRaw(k.generation,into:b); try key.encodeRaw(k.key,into:b) },decode:{ r in ScopedKey(epoch:try r.value(ValueCodecs.u64),generation:try r.value(ValueCodecs.u64),key:try r.value(key)) })
}
let legacyLifecycleMessages: [String:String] = [
"time_backwards":"logical time moved backwards",
"missing_before_advance":"required settlement missing before time advance",
"key_reused":"correlation key reused",
"began_after_deadline":"obligation began after its deadline",
"cancel_without_obligation":"cancellation without obligation",
"settlement_after_cancel":"settlement after cancellation",
"duplicate_settlement":"duplicate settlement",
"settlement_after_deadline":"settlement after deadline",
"settlement_without_obligation":"settlement without obligation",
"stale_or_premature_publication":"stale or premature publication",
"resource_already_acquired":"resource already acquired",
"release_before_settlement":"resource released before settlement",
"duplicate_release":"duplicate resource release",
"release_without_acquisition":"release without acquisition",
"missing_in_prior_history":"required settlement missing in prior history",
"missing_at_deadline":"required settlement missing at deadline",
]
