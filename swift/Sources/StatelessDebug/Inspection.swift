import Statelessness

public struct SnapshotID: Equatable {
    public let session: UInt64
    public let revision: UInt64
    public let sequence: UInt64
    public init(session: UInt64, revision: UInt64, sequence: UInt64) { self.session = session; self.revision = revision; self.sequence = sequence }
}
public struct SourceSite: Equatable {
    public let file: String
    public let line: UInt32
    public let column: UInt32
    public init(file: String,line: UInt32,column: UInt32) { self.file = file; self.line = line; self.column = column }
    public static func == (a: Self,b: Self) -> Bool { exactText(a.file,b.file) && a.line == b.line && a.column == b.column }
}
public struct DisplaySchema: Equatable {
    public let name: String
    public let version: UInt32
    public let source: SourceSite?
    public init(name: String, version: UInt32 = 1,source: SourceSite? = nil) { self.name = name; self.version = version; self.source = source }
    public static func == (a: Self,b: Self) -> Bool { exactText(a.name,b.name) && a.version == b.version && a.source == b.source }
}
public enum PathSegment: Equatable {
    case field(ExactString), variant(ExactString), index(UInt64), mapKey(ExactString), typedMapKey(InspectMapKey)
}
public enum DisplayScalar: Equatable {
    case unit, bool(Bool), character(UInt32)
    /// Kind and decimal text preserve i128/u128/full-width quantity identity.
    case integer(kind: ExactString, decimal: ExactString)
    case float(kind: ExactString, decimal: ExactString, bits: UInt64)
}
public enum IncompleteReason: String { case pagination, sampled, observationGap, schemaChanged, work, depth, nodes, bytes, valueLimit, redacted, opaque, unavailable }
public enum Completeness: Equatable { case complete, partial(IncompleteReason) }
public enum MapOrdering: Equatable { case stable, unstable }
public enum NodeKind: Equatable {
    case scalar(DisplayScalar), string(preview: ExactString,totalBytes: Int), bytes(preview: [UInt8],totalBytes: Int)
    case object(ExactString), enumeration(name: ExactString,variantID: ExactString,variantLabel: ExactString)
    case sequence(ExactString), map(MapOrdering), opaque(ExactString), redacted, unavailable, truncated(IncompleteReason)
}
public struct PageRequest { public let offset: Int; public let limit: Int; public init(offset: Int = 0,limit: Int = 100) { self.offset = offset; self.limit = limit } }
public struct PageInfo: Equatable { public let offset: Int; public let returned: Int; public let total: Int; public let nextOffset: Int? }
public struct InspectChild: Equatable {
    public let segment: PathSegment
    public let label: ExactString
    public let node: InspectNode
    public let source: SourceSite?
    public init(segment: PathSegment,label: ExactString,node: InspectNode,source: SourceSite? = nil) { self.segment = segment; self.label = label; self.node = node; self.source = source }
}
public struct InspectNode: Equatable {
    public let kind: NodeKind
    public let children: [InspectChild]
    public let childCount: Int?
    public let page: PageInfo?
    public let completeness: Completeness
    public init(kind: NodeKind, children: [InspectChild] = [], childCount: Int? = nil, page: PageInfo? = nil, completeness: Completeness = .complete) { self.kind = kind; self.children = children; self.childCount = childCount; self.page = page; self.completeness = completeness }
    public var isComplete: Bool {
        guard completeness == .complete, children.allSatisfy({ $0.node.isComplete }), childCount == nil || childCount == children.count else { return false }
        if let page, page.offset != 0 || page.returned != children.count || page.total != children.count || page.nextOffset != nil { return false }
        switch kind {
        case .opaque, .redacted, .unavailable, .truncated, .map(.unstable): return false
        case let .string(preview,total): return preview.value.utf8.count == total
        case let .bytes(preview,total): return preview.count == total
        case .object,.enumeration,.sequence,.map: return childCount == children.count
        default: return true
        }
    }
}
public struct InspectLimits: Equatable {
    public var maxWork = 10_000
    public var maxDepth = 32
    public var maxNodes = 1024
    public var maxBytes = 256*1024
    public var maxValueBytes = 4096
    public var maxPageSize = 100
    public init() {}
}
public enum InspectError: Error { case invalidMetadata, invalidLimits, budgetExceeded, pathNotFound, schemaMismatch, invalidPage, redactedPath }
public enum DiffKind: String { case added, removed, unchanged, changed, unknown }
public func compareNodes(_ before: InspectNode?, _ after: InspectNode?) -> DiffKind {
    if before.map({ !$0.isComplete }) == true || after.map({ !$0.isComplete }) == true { return .unknown }
    switch (before,after) { case (nil,.some): return .added; case (.some,nil): return .removed; case let (a?,b?): return a == b ? .unchanged : .changed; default: return .unknown }
}
/// Owns bounded projection accounting. Application closures remain cooperative;
/// these limits do not bound their allocations, process RSS or execution time.
public final class InspectContext {
    public let limits: InspectLimits
    public private(set) var work = 0
    public private(set) var nodes = 0
    public private(set) var bytes = 0
    private var depth = 0
    public init(limits: InspectLimits = InspectLimits()) throws {
        guard limits.maxWork >= 0,limits.maxDepth >= 0,limits.maxNodes >= 0,limits.maxBytes >= 0,limits.maxValueBytes >= 0,limits.maxPageSize >= 0 else { throw InspectError.invalidLimits }
        self.limits = limits
    }
    public func chargeWork(_ amount: Int = 1) throws { guard amount >= 0, amount <= limits.maxWork-work else { throw InspectError.budgetExceeded }; work += amount }
    private func charge(_ payload: Int) throws {
        try chargeWork()
        guard depth <= limits.maxDepth, nodes < limits.maxNodes, payload >= 0, payload <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }
        nodes += 1; bytes += payload
    }
    public func scalar(_ value: DisplayScalar) throws -> InspectNode {
        try validateDisplayScalar(value)
        let payload: Int
        switch value { case let .integer(kind,decimal): payload = kind.value.utf8.count + decimal.value.utf8.count; case let .float(kind,decimal,_): payload = kind.value.utf8.count + decimal.value.utf8.count; default: payload = 16 }
        guard payload <= limits.maxValueBytes else { throw InspectError.budgetExceeded }
        try charge(payload); return InspectNode(kind: .scalar(value))
    }
    public func string(_ value: String) throws -> InspectNode {
        let maximum = min(limits.maxValueBytes,limits.maxBytes-bytes)
        var preview = "", count = 0
        for scalar in value.unicodeScalars { let text = String(scalar); guard text.utf8.count <= maximum-count else { break }; preview += text; count += text.utf8.count }
        try charge(count)
        return InspectNode(kind: .string(preview: ExactString(preview),totalBytes: value.utf8.count),completeness: count == value.utf8.count ? .complete : .partial(.valueLimit))
    }
    public func bytes(_ value: [UInt8]) throws -> InspectNode {
        let count = min(value.count,limits.maxValueBytes,limits.maxBytes-bytes)
        try charge(count)
        return InspectNode(kind: .bytes(preview: Array(value.prefix(count)),totalBytes: value.count),completeness: count == value.count ? .complete : .partial(.valueLimit))
    }
    /// Redaction skips evaluation entirely, including a closure that throws.
    public func field(_ name: String, redacted: Bool = false,source: SourceSite? = nil, project: (InspectContext) throws -> InspectNode) throws -> InspectChild {
        try chargeWork()
        if let source { guard source.file.utf8.count <= limits.maxValueBytes,source.file.utf8.count <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }; bytes += source.file.utf8.count }
        guard name.utf8.count <= limits.maxValueBytes, name.utf8.count <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }
        bytes += name.utf8.count
        if redacted { try charge(0); return InspectChild(segment: .field(ExactString(name)),label: ExactString(name),node: InspectNode(kind: .redacted,completeness: .partial(.redacted)),source: source) }
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        return try InspectChild(segment: .field(ExactString(name)),label: ExactString(name),node: project(self),source: source)
    }
    public func object(_ name: String, fields: (InspectContext) throws -> [InspectChild]) throws -> InspectNode {
        guard name.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }
        try charge(name.utf8.count)
        let children = try fields(self)
        return InspectNode(kind: .object(ExactString(name)),children: children,childCount: children.count)
    }
    public func sequence(name: String, total: Int, page: PageRequest = PageRequest(), project: (Int,InspectContext) throws -> InspectNode) throws -> InspectNode {
        guard total >= 0,page.offset >= 0,page.offset <= total,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        guard name.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }
        try charge(name.utf8.count)
        let end = page.offset + min(page.limit,total-page.offset)
        var children: [InspectChild] = []
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        for i in page.offset..<end { try chargeWork(); children.append(try InspectChild(segment: .index(UInt64(i)),label: ExactString(String(i)),node: project(i,self))) }
        let info = PageInfo(offset: page.offset,returned: children.count,total: total,nextOffset: end < total ? end : nil)
        return InspectNode(kind: .sequence(ExactString(name)),children: children,childCount: total,page: info,completeness: page.offset == 0 && end == total ? .complete : .partial(.pagination))
    }
}
public struct InspectResult {
    public let snapshot: SnapshotID
    public let schema: DisplaySchema
    public let path: [PathSegment]
    public let node: InspectNode
    public init(snapshot: SnapshotID,schema: DisplaySchema,path: [PathSegment] = [],node: InspectNode) { self.snapshot = snapshot; self.schema = schema; self.path = path; self.node = node }
}
public func diff(_ before: InspectResult,_ after: InspectResult) -> DiffKind {
    guard before.schema == after.schema,before.path == after.path else { return .unknown }
    return compareNodes(before.node,after.node)
}

public enum InspectMapKey: Equatable {
    case string(ExactString),integer(kind: ExactString,decimal: ExactString),bool(Bool),character(UInt32)
    fileprivate var segment: PathSegment {
        switch self {
        case .string: return .typedMapKey(self)
        case .integer,.bool,.character: return .typedMapKey(self)
        }
    }
}
public struct InspectMapEntry { public let key: InspectMapKey; public let label: String; public let project: (InspectContext) throws -> InspectNode
    public init(key: InspectMapKey,label: String,project: @escaping (InspectContext) throws -> InspectNode) { self.key = key; self.label = label; self.project = project }
}
extension InspectContext {
    public func opaque(_ label: String) throws -> InspectNode {
        guard label.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }; try charge(label.utf8.count)
        return InspectNode(kind: .opaque(ExactString(label)),completeness: .partial(.opaque))
    }
    public func unavailable() throws -> InspectNode { try charge(0); return InspectNode(kind: .unavailable,completeness: .partial(.unavailable)) }
    public func enumeration(name: String,variantID: String,variantLabel: String,fields: (InspectContext) throws -> [InspectChild]) throws -> InspectNode {
        for value in [name,variantID,variantLabel] { guard value.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded } }
        try charge(name.utf8.count+variantID.utf8.count+variantLabel.utf8.count)
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        let object = try self.object(variantLabel,fields: fields)
        let child = InspectChild(segment: .variant(ExactString(variantID)),label: ExactString(variantLabel),node: object)
        return InspectNode(kind: .enumeration(name: ExactString(name),variantID: ExactString(variantID),variantLabel: ExactString(variantLabel)),children: [child],childCount: 1)
    }
    /// Iterator work and payload projection are bounded independently. Stable
    /// ordering is a host declaration; unstable maps always compare Unknown.
    public func map(ordering: MapOrdering,total: Int,page: PageRequest = PageRequest(),next: () throws -> InspectMapEntry?) throws -> InspectNode {
        guard total >= 0,page.offset >= 0,page.offset <= total,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        try charge(0)
        for _ in 0..<page.offset { try chargeWork(); guard try next() != nil else { throw InspectError.pathNotFound } }
        let end = page.offset+min(page.limit,total-page.offset)
        var children: [InspectChild] = []
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        for _ in page.offset..<end {
            try chargeWork(); guard let entry = try next() else { throw InspectError.pathNotFound }
            try validateMapKey(entry.key)
            let keyBytes: Int
            switch entry.key {
            case let .string(value): keyBytes = value.value.utf8.count
            case let .integer(kind,decimal): keyBytes = kind.value.utf8.count + decimal.value.utf8.count
            case .bool: keyBytes = 1
            case .character: keyBytes = 4
            }
            guard keyBytes <= limits.maxValueBytes,keyBytes <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }
            bytes += keyBytes
            guard entry.label.utf8.count <= limits.maxValueBytes,entry.label.utf8.count <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }
            bytes += entry.label.utf8.count
            children.append(try InspectChild(segment: entry.key.segment,label: ExactString(entry.label),node: entry.project(self)))
        }
        return InspectNode(kind: .map(ordering),children: children,childCount: total,page: PageInfo(offset: page.offset,returned: children.count,total: total,nextOffset: end < total ? end : nil),completeness: page.offset == 0 && end == total ? .complete : .partial(.pagination))
    }
}
public struct InspectChange {
    public let path: [PathSegment]
    public let kind: DiffKind
}
public struct StructuralDiff {
    public let kind: DiffKind
    public let changes: [InspectChange]
    public let truncated: Bool
}
public func structuralDiff(_ before: InspectResult,_ after: InspectResult,maxChanges: Int = 1000,maxWork: Int = 10_000) throws -> StructuralDiff {
    guard maxChanges >= 0,maxWork >= 0 else { throw InspectError.invalidLimits }
    guard before.schema == after.schema,before.path == after.path else { return StructuralDiff(kind: .unknown,changes: [],truncated: false) }
    var remaining = maxWork,changes: [InspectChange] = [],truncated = false
    func consume() -> Bool { guard remaining > 0 else { truncated = true; return false }; remaining -= 1; return true }
    func complete(_ root: InspectNode) -> Bool {
        var stack = [root]
        while let node = stack.popLast() {
            guard consume() else { return false }
            guard node.completeness == .complete,node.childCount == nil || node.childCount == node.children.count else { return false }
            if let page = node.page,page.offset != 0 || page.returned != node.children.count || page.total != node.children.count || page.nextOffset != nil { return false }
            switch node.kind {
            case .opaque,.redacted,.unavailable,.truncated,.map(.unstable): return false
            case let .string(s,n): if s.value.utf8.count != n { return false }
            case let .bytes(b,n): if b.count != n { return false }
            case .object,.enumeration,.sequence,.map: if node.childCount != node.children.count { return false }
            default: break
            }
            for child in node.children { guard consume() else { return false }; stack.append(child.node) }
        }
        return true
    }
    guard complete(before.node),complete(after.node) else { return StructuralDiff(kind: .unknown,changes: [],truncated: truncated) }
    func add(_ path: [PathSegment],_ kind: DiffKind) { guard changes.count < maxChanges else { truncated = true; return }; changes.append(InspectChange(path: path,kind: kind)) }
    func visit(_ a: InspectNode?,_ b: InspectNode?,_ path: [PathSegment]) {
        guard consume() else { return }
        guard let a else { if b != nil { add(path,.added) }; return }
        guard let b else { add(path,.removed); return }
        guard a.kind == b.kind else { add(path,.changed); return }
        for child in a.children {
            var selected: InspectNode?
            for candidate in b.children { guard consume() else { return }; if candidate.segment == child.segment { selected = candidate.node; break } }
            visit(child.node,selected,path+[child.segment])
        }
        for child in b.children {
            var found = false
            for candidate in a.children { guard consume() else { return }; if candidate.segment == child.segment { found = true; break } }
            if !found { visit(nil,child.node,path+[child.segment]) }
        }
    }
    visit(before.node,after.node,before.path)
    return StructuralDiff(kind: truncated ? .unknown : changes.isEmpty ? .unchanged : .changed,changes: changes,truncated: truncated)
}
/// Resolve only a bounded projected tree, never arbitrary application reflection.
public func resolve(_ result: InspectResult,path: [PathSegment],schemaVersion: UInt32,maxWork: Int = 10_000) throws -> InspectResult {
    guard result.schema.version == schemaVersion else { throw InspectError.schemaMismatch }
    guard path.count <= 32,maxWork >= 0,path.starts(with: result.path) else { throw InspectError.pathNotFound }
    var node = result.node,work = 0
    for segment in path.dropFirst(result.path.count) {
        if node.kind == .redacted { throw InspectError.redactedPath }
        var selected: InspectNode?
        for child in node.children { guard work < maxWork else { throw InspectError.budgetExceeded }; work += 1; if child.segment == segment { selected = child.node; break } }
        guard let selected else { throw InspectError.pathNotFound }; node = selected
    }
    if node.kind == .redacted { throw InspectError.redactedPath }
    return InspectResult(snapshot: result.snapshot,schema: result.schema,path: path,node: node)
}

/// Conservative retained payload accounting, including typed key text. Callback
/// allocation and allocator headers are outside this payload estimate.
func nodeRetainedPayloadBytes(_ root: InspectNode) -> Int {
    var stack = [root],total = 0
    func add(_ count: Int) { total = count > Int.max-total ? Int.max : total+count }
    func keyBytes(_ key: InspectMapKey) -> Int {
        switch key { case let .string(s): return s.value.utf8.count; case let .integer(k,d): return k.value.utf8.count+d.value.utf8.count; default: return 16 }
    }
    while let node = stack.popLast() {
        add(128)
        switch node.kind {
        case let .string(s,_): add(s.value.utf8.count)
        case let .bytes(b,_): add(b.count)
        case let .object(s),let .sequence(s),let .opaque(s): add(s.value.utf8.count)
        case let .enumeration(n,i,l): add(n.value.utf8.count); add(i.value.utf8.count); add(l.value.utf8.count)
        case let .scalar(.integer(k,d)),let .scalar(.float(k,d,_)): add(k.value.utf8.count); add(d.value.utf8.count)
        default: break
        }
        for child in node.children {
            add(64); add(child.label.value.utf8.count); add(child.source?.file.utf8.count ?? 0)
            switch child.segment { case let .field(s),let .variant(s),let .mapKey(s): add(s.value.utf8.count); case let .typedMapKey(k): add(keyBytes(k)); default: break }
            stack.append(child.node)
        }
    }
    return total
}

/// Validate retained projection work/payload without trusting a hand-constructed
/// node from an application callback. Stops scanning at the configured budget.
func validateProjection(_ root: InspectNode,limits: InspectLimits) throws {
    guard limits.maxNodes >= 0,limits.maxWork >= 0,limits.maxDepth >= 0,limits.maxBytes >= 0 else { throw InspectError.invalidLimits }
    var stack: [(InspectNode,Int)] = [(root,0)],nodes = 0,work = 0,bytes = 0
    func charge(_ count: Int) throws { guard count >= 0,count <= limits.maxBytes-bytes else { throw InspectError.budgetExceeded }; bytes += count }
    func text(_ value: String) throws { guard value.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }; try charge(value.utf8.count) }
    func segment(_ value: PathSegment) throws {
        switch value {
        case let .field(s),let .variant(s),let .mapKey(s): try text(s.value)
        case let .typedMapKey(key): try validateMapKey(key); switch key { case let .string(s): try text(s.value); case let .integer(k,d): try text(k.value); try text(d.value); default: try charge(16) }
        default: try charge(16)
        }
    }
    while let (node,depth) = stack.popLast() {
        guard nodes < limits.maxNodes,work < limits.maxWork,depth <= limits.maxDepth else { throw InspectError.budgetExceeded }; nodes += 1; work += 1; try charge(128)
        switch node.kind {
        case let .scalar(value): try validateDisplayScalar(value); switch value { case let .integer(k,d),let .float(k,d,_): try text(k.value); try text(d.value); default: break }
        case let .string(s,_): try text(s.value)
        case let .bytes(b,_): guard b.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }; try charge(b.count)
        case let .object(s),let .sequence(s),let .opaque(s): try text(s.value)
        case let .enumeration(n,i,l): try text(n.value); try text(i.value); try text(l.value)
        default: break
        }
        guard node.children.count <= limits.maxNodes-nodes, node.children.count <= limits.maxWork-work else { throw InspectError.budgetExceeded }
        for child in node.children { work += 1; try charge(64); try text(child.label.value); if let source = child.source { try text(source.file) }; try segment(child.segment); stack.append((child.node,depth+1)) }
    }
}

/// Runtime admission for explicitly constructed display values. This validates
/// display kinds/ranges; it does not introduce a canonical floating-point codec.
private func validateInteger(kind: String,decimal: String) throws {
    let unsigned: Bool,bits: Int
    switch kind {
    case "u8": unsigned = true; bits = 8
    case "u16": unsigned = true; bits = 16
    case "u32": unsigned = true; bits = 32
    case "u64": unsigned = true; bits = 64
    case "u128": unsigned = true; bits = 128
    case "usize": unsigned = true; bits = UInt.bitWidth
    case "i8": unsigned = false; bits = 8
    case "i16": unsigned = false; bits = 16
    case "i32": unsigned = false; bits = 32
    case "i64": unsigned = false; bits = 64
    case "i128": unsigned = false; bits = 128
    case "isize": unsigned = false; bits = Int.bitWidth
    default: throw InspectError.invalidMetadata
    }
    if bits == 128 {
        if unsigned { guard let value = try? TraceUInt128(decimal: decimal),exactText(value.description,decimal) else { throw InspectError.invalidMetadata } }
        else { guard let value = try? TraceInt128(decimal: decimal),exactText(value.description,decimal) else { throw InspectError.invalidMetadata } }
    } else if unsigned {
        guard let value = UInt64(decimal),exactText(String(value),decimal),bits == 64 || value <= UInt64.max >> (64-bits) else { throw InspectError.invalidMetadata }
    } else {
        guard let value = Int64(decimal),exactText(String(value),decimal) else { throw InspectError.invalidMetadata }
        if bits < 64 { let max = (Int64(1) << (bits-1))-1; guard value >= -max-1,value <= max else { throw InspectError.invalidMetadata } }
    }
}
private func validCharacter(_ scalar: UInt32) -> Bool { scalar <= 0x10FFFF && !(0xD800...0xDFFF).contains(scalar) }
private func validateDisplayScalar(_ value: DisplayScalar) throws {
    switch value {
    case let .integer(kind,decimal): try validateInteger(kind: kind.value,decimal: decimal.value)
    case let .character(scalar): guard validCharacter(scalar) else { throw InspectError.invalidMetadata }
    case let .float(kind,decimal,bits):
        if exactText(kind.value,"f32") {
            guard let bits = UInt32(exactly: bits),let text = Float(decimal.value) else { throw InspectError.invalidMetadata }
            let value = Float(bitPattern: bits)
            guard (text.isNaN && value.isNaN) || text.bitPattern == bits else { throw InspectError.invalidMetadata }
        } else if exactText(kind.value,"f64") {
            guard let text = Double(decimal.value) else { throw InspectError.invalidMetadata }
            let value = Double(bitPattern: bits)
            guard (text.isNaN && value.isNaN) || text.bitPattern == bits else { throw InspectError.invalidMetadata }
        } else { throw InspectError.invalidMetadata }
    default: break
    }
}
private func validateMapKey(_ value: InspectMapKey) throws {
    switch value {
    case let .integer(kind,decimal): try validateInteger(kind: kind.value,decimal: decimal.value)
    case let .character(scalar): guard validCharacter(scalar) else { throw InspectError.invalidMetadata }
    default: break
    }
}
