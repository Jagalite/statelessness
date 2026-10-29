/// Lifecycle obligations are driven by explicit observations, never wall time.
public struct ScopedKey<K: Comparable>: Comparable {
    public let epoch: UInt64
    public let generation: UInt64
    public let key: K
    public init(epoch: UInt64, generation: UInt64, key: K) { self.epoch = epoch; self.generation = generation; self.key = key }
    public static func < (a: Self, b: Self) -> Bool { a.epoch != b.epoch ? a.epoch < b.epoch : a.generation != b.generation ? a.generation < b.generation : a.key < b.key }
}
public enum LifecycleEvent<K> {
    case begin(K, deadline: UInt64), cancel(K), settle(K), publish(K), acquire(K), release(K), tick(UInt64), fence(before: UInt64)
}
public struct LifecycleObligation: Equatable {
    public var deadline: UInt64
    public var cancelled = false
    public var settled = false
    public var published = false
    public init(deadline: UInt64) { self.deadline = deadline }
    public var pending: Bool { !cancelled && !settled }
}
public struct LifecycleViolation<K> {
    public let legacy: String?
    public init(key:K?,code:String,event:String,observedAt:UInt64,prior:LifecycleObligation?,legacy:String? = nil) { self.key=key;self.code=code;self.event=event;self.observedAt=observedAt;self.prior=prior;self.legacy=legacy }
    public let key: K?
    public let code: String
    public let event: String
    public let observedAt: UInt64
    public let prior: LifecycleObligation?
}
public struct LifecycleHistory<K> {
    public var now: UInt64 = 0
    public var retiredBefore: UInt64 = 0
    public var obligations: [(K, LifecycleObligation)] = []
    public var resources: [(K, Bool)] = []
    public var violations: [LifecycleViolation<K>] = []
    public init() {}
}
public struct RetentionLimits {
    public let obligations: Int
    public let resources: Int
    public let violations: Int
    public init(obligations: Int = 1024, resources: Int = 1024, violations: Int = 1024) { self.obligations = obligations; self.resources = resources; self.violations = violations }
}
public struct LifecycleRetention<K> {
    public let activeObligations: Int
    public let historicalObligations: Int
    public let acquiredResources: Int
    public let historicalResources: Int
    public let oldestUnresolved: (K, UInt64)?
    public let retiredBefore: UInt64
}
/// Supply an epoch extractor only for explicitly fenced, scoped keys. Key copy
/// closures preserve application ownership, including reference-valued keys.
public struct LifecycleMonitor<K> {
    public let limits: RetentionLimits
    public let compare: (K, K) -> Int
    public let cloneKey: (K) throws -> K
    public let epoch: ((K) -> UInt64)?
    public init(limits: RetentionLimits = RetentionLimits(), compare: @escaping (K,K) -> Int, cloneKey: @escaping (K) throws -> K, epoch: ((K) -> UInt64)? = nil) {
        self.limits = limits; self.compare = compare; self.cloneKey = cloneKey; self.epoch = epoch
    }
    public func cloneHistory(_ h: LifecycleHistory<K>) throws -> LifecycleHistory<K> {
        var h = h
        h.obligations = try h.obligations.map { (try cloneKey($0.0), $0.1) }
        h.resources = try h.resources.map { (try cloneKey($0.0), $0.1) }
        h.violations = try h.violations.map { LifecycleViolation(key: try $0.key.map(cloneKey), code: $0.code, event: $0.event, observedAt: $0.observedAt, prior: $0.prior, legacy: $0.legacy) }
        return h
    }
    public func validate(_ h: LifecycleHistory<K>) throws {
        guard limits.obligations >= 0, limits.resources >= 0, limits.violations >= 0,
              h.obligations.count <= limits.obligations, h.resources.count <= limits.resources, h.violations.count <= limits.violations else { throw ModelError("lifecycle retention limit; verification incomplete") }
        if let epoch = epoch {
            for keys in [h.obligations.map { $0.0 }, h.resources.map { $0.0 }] {
                for (i,k) in keys.enumerated() { if epoch(k) < h.retiredBefore || (i > 0 && compare(keys[i-1],k) >= 0) { throw ModelError("noncanonical indexed history") } }
            }
            if h.obligations.contains(where: { $0.1.published && !$0.1.settled }) { throw ModelError("invalid publication history") }
        }
    }
    public func retention(_ h: LifecycleHistory<K>) throws -> LifecycleRetention<K> {
        let active = h.obligations.filter { $0.1.pending }, acquired = h.resources.filter { $0.1 }.count
        let oldest = active.min { $0.1.deadline < $1.1.deadline }
        return LifecycleRetention(activeObligations: active.count, historicalObligations: h.obligations.count-active.count, acquiredResources: acquired, historicalResources: h.resources.count-acquired, oldestUnresolved: try oldest.map { (try cloneKey($0.0), $0.1.deadline) }, retiredBefore: h.retiredBefore)
    }
    private func find<V>(_ entries:[(K,V)],_ key:K) -> Int? {
        if epoch == nil { return entries.firstIndex { compare($0.0,key) == 0 } }
        var low=0,high=entries.count
        while low<high { let mid=(low+high)/2; if compare(entries[mid].0,key)<0 { low=mid+1 } else { high=mid } }
        return low<entries.count && compare(entries[low].0,key)==0 ? low : nil
    }
    private func overdue(_ h: LifecycleHistory<K>, _ time: UInt64, strict: Bool = false) -> K? {
        h.obligations.first { $0.1.pending && (strict ? $0.1.deadline < time : $0.1.deadline <= time) }?.0
    }
    private func violation(_ h: inout LifecycleHistory<K>, _ key: K?, _ code: String, _ event: String) throws {
        guard h.violations.count < limits.violations else { throw ModelError("lifecycle violation limit; verification incomplete") }
        let prior = key.flatMap { k in find(h.obligations,k).map { h.obligations[$0].1 } }
        h.violations.append(LifecycleViolation(key: try key.map(cloneKey), code: code, event: event, observedAt: h.now, prior: prior, legacy: epoch == nil ? legacyLifecycleMessages[code] ?? code : nil))
    }
    public func advance(_ before: LifecycleHistory<K>, events: [LifecycleEvent<K>]) throws -> LifecycleHistory<K> {
        try validate(before); var h = try cloneHistory(before)
        if let k = overdue(h,h.now) { try violation(&h,k,"missing_in_prior_history","observation") }
        for event in events {
            if case let .fence(time) = event {
                guard let epoch = epoch else { throw ModelError("legacy history has no fence") }
                if time < h.retiredBefore { try violation(&h,nil,"fence_backwards","fence"); continue }
                if let k = h.obligations.first(where: { epoch($0.0) < time && $0.1.pending })?.0 { try violation(&h,k,"fence_pending_obligation","fence"); continue }
                if let k = h.resources.first(where: { epoch($0.0) < time && $0.1 })?.0 { try violation(&h,k,"fence_acquired_resource","fence"); continue }
                h.obligations.removeAll { epoch($0.0) < time }; h.resources.removeAll { epoch($0.0) < time }; h.retiredBefore = time; continue
            }
            let key: K?, kind: String
            switch event {
            case let .begin(k,_): key = k; kind = "begin"
            case let .cancel(k): key = k; kind = "cancel"
            case let .settle(k): key = k; kind = "settle"
            case let .publish(k): key = k; kind = "publish"
            case let .acquire(k): key = k; kind = "acquire"
            case let .release(k): key = k; kind = "release"
            case .tick: key = nil; kind = "tick"
            case .fence: continue
            }
            if let k = key, let epoch = epoch, epoch(k) < h.retiredBefore { try violation(&h,k,"retired_epoch_reference",kind); continue }
            let n = key.flatMap { k in find(h.obligations,k) }
            let r = key.flatMap { k in find(h.resources,k) }
            switch event {
            case let .tick(time):
                if time < h.now { try violation(&h,nil,"time_backwards",kind) }
                else { if let k = overdue(h,time,strict: true) { try violation(&h,k,"missing_before_advance",kind) }; h.now = time }
            case let .begin(k,deadline):
                if n != nil { try violation(&h,k,"key_reused",kind) }
                else { guard h.obligations.count < limits.obligations else { throw ModelError("obligation limit; explicit fence required; verification incomplete") }; h.obligations.append((try cloneKey(k),LifecycleObligation(deadline: deadline))); if deadline < h.now { try violation(&h,k,"began_after_deadline",kind) } }
            case .cancel:
                if let n = n { h.obligations[n].1.cancelled = true } else { try violation(&h,key,"cancel_without_obligation",kind) }
            case .settle:
                if let n = n {
                    let o = h.obligations[n].1
                    if o.cancelled { try violation(&h,key,"settlement_after_cancel",kind) }
                    else if o.settled { try violation(&h,key,"duplicate_settlement",kind) }
                    else { if epoch == nil { h.obligations[n].1.settled = true }; if o.deadline < h.now { try violation(&h,key,"settlement_after_deadline",kind) }; h.obligations[n].1.settled = true }
                } else { try violation(&h,key,"settlement_without_obligation",kind) }
            case .publish:
                if let n = n, !h.obligations[n].1.cancelled, h.obligations[n].1.settled {
                    if epoch != nil { if h.obligations[n].1.published { try violation(&h,key,"duplicate_publication",kind) } else { h.obligations[n].1.published = true } }
                } else { try violation(&h,key,"stale_or_premature_publication",kind) }
            case let .acquire(k):
                if let r = r { if h.resources[r].1 { try violation(&h,key,"resource_already_acquired",kind) } else { h.resources[r].1 = true } }
                else { guard h.resources.count < limits.resources else { throw ModelError("resource limit; verification incomplete") }; h.resources.append((try cloneKey(k),true)) }
            case .release:
                if let n = n, h.obligations[n].1.pending { try violation(&h,key,"release_before_settlement",kind) }
                if let r = r { if h.resources[r].1 { h.resources[r].1 = false } else { try violation(&h,key,"duplicate_release",kind) } }
                else { try violation(&h,key,"release_without_acquisition",kind) }
            case .fence: break
            }
            if epoch != nil { h.obligations.sort { compare($0.0,$1.0) < 0 }; h.resources.sort { compare($0.0,$1.0) < 0 } }
        }
        if let k = overdue(h,h.now) { try violation(&h,k,"missing_at_deadline","observation") }; return h
    }
    public func checks(_ h: LifecycleHistory<K>) throws -> [Check] {
        try validate(h); let prefix = epoch == nil ? "lifecycle" : "lifecycle.indexed", n = h.obligations.filter { $0.1.pending }.count
        return [h.violations.isEmpty && overdue(h,h.now) == nil ? .passed(prefix+".safety") : .failed(prefix+".safety",h.violations.isEmpty ? (epoch == nil ? "required settlement missing at deadline" : "missing_at_deadline") : h.violations.map { epoch == nil ? $0.legacy ?? legacyLifecycleMessages[$0.code] ?? $0.code : $0.code }.joined(separator: "; ")), n == 0 ? .passed(prefix+".obligations") : .skipped(prefix+".obligations","\(n) pending obligations in finite history")]
    }
    public func equalHistory(_ a: LifecycleHistory<K>, _ b: LifecycleHistory<K>) -> Bool {
        guard a.now == b.now, a.retiredBefore == b.retiredBefore, a.obligations.count == b.obligations.count, a.resources.count == b.resources.count, a.violations.count == b.violations.count else { return false }
        for (x,y) in zip(a.obligations,b.obligations) { if compare(x.0,y.0) != 0 || x.1 != y.1 { return false } }
        for (x,y) in zip(a.resources,b.resources) { if compare(x.0,y.0) != 0 || x.1 != y.1 { return false } }
        for (x,y) in zip(a.violations,b.violations) {
            if epoch == nil { if !exactText(x.legacy ?? legacyLifecycleMessages[x.code] ?? x.code,y.legacy ?? legacyLifecycleMessages[y.code] ?? y.code) { return false }; continue }
            switch (x.key,y.key) { case (nil,nil): break; case let (.some(k),.some(l)): if compare(k,l) != 0 { return false }; default: return false }
            if !exactText(x.code,y.code) || !exactText(x.event,y.event) || x.observedAt != y.observedAt || x.prior != y.prior { return false }
        }
        return true
    }
    public func oracle<S,I,O>(metadata: @escaping () throws -> Metadata, inputEvents: @escaping (I,Disposition) throws -> [LifecycleEvent<K>], outputEvents: @escaping ([O]) throws -> [LifecycleEvent<K>]) -> Oracle<S,LifecycleHistory<K>,I,O> {
        Oracle(metadata: { let m = try metadata(); let name = self.epoch == nil ? "\(m.name);history-limit=\(self.limits.obligations)" : "indexed-lifecycle-v1:\(m.name.utf8.count):\(m.name);limits=\(self.limits.obligations),\(self.limits.resources),\(self.limits.violations);diagnostics=native-codes-v1"; return Metadata(name:name,modelVersion:m.modelVersion,propertiesVersion:m.propertiesVersion,codecVersion:m.codecVersion,build:m.build+";lifecycle-native") }, initialState: { LifecycleHistory() }, advance: { h,i,o,d in try self.advance(h,events: inputEvents(i,d)+outputEvents(o)) }, checkState: { h,_ in try self.checks(h) }, cloneHistory: cloneHistory, equalHistory: equalHistory)
    }
}
public func indexedLifecycleMonitor<K: Comparable>(limits: RetentionLimits = RetentionLimits(), cloneKey: @escaping (K) throws -> K) -> LifecycleMonitor<ScopedKey<K>> {
    LifecycleMonitor(limits: limits, compare: { $0 < $1 ? -1 : $1 < $0 ? 1 : 0 }, cloneKey: { k in guard k.key == k.key else { throw ModelError("non-reflexive lifecycle key") }; return ScopedKey(epoch: k.epoch, generation: k.generation, key: try cloneKey(k.key)) }, epoch: { $0.epoch })
}

/// Exact UTF-8 key identity/order, including canonically equivalent spellings.
public struct LifecycleTextKey: Comparable {
    public let value: String
    public init(_ value: String) { self.value = value }
    public static func == (a: Self, b: Self) -> Bool { exactText(a.value,b.value) }
    public static func < (a: Self, b: Self) -> Bool { a.value.utf8.lexicographicallyPrecedes(b.value.utf8) }
}
