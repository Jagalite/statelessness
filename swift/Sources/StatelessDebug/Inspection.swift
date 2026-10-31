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
public enum PathSegment: Hashable {
    case field(ExactString), variant(ExactString), index(UInt64), mapKey(ExactString), typedMapKey(InspectMapKey)
    public static func == (a: Self,b: Self) -> Bool {
        switch (a,b) {
        case let (.field(x),.field(y)),let (.variant(x),.variant(y)): return x == y
        case let (.index(x),.index(y)): return x == y
        case let (.mapKey(x),.mapKey(y)): return x == y
        case let (.typedMapKey(x),.typedMapKey(y)): return x == y
        case let (.mapKey(x),.typedMapKey(.string(y))),let (.typedMapKey(.string(y)),.mapKey(x)): return x == y
        default: return false
        }
    }
    public func hash(into hasher: inout Hasher) {
        switch self {
        case let .field(value): hasher.combine(0); hasher.combine(value)
        case let .variant(value): hasher.combine(1); hasher.combine(value)
        case let .index(value): hasher.combine(2); hasher.combine(value)
        case let .mapKey(value): hasher.combine(3); hasher.combine(InspectMapKey.string(value))
        case let .typedMapKey(value): hasher.combine(3); hasher.combine(value)
        }
    }
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
public struct PageRequest: Equatable { public let offset: Int; public let limit: Int; public init(offset: Int = 0,limit: Int = 100) { self.offset = offset; self.limit = limit } }
public struct PageInfo: Equatable { public let offset: Int; public let returned: Int; public let total: Int; public let nextOffset: Int?
    public init(offset: Int,returned: Int,total: Int,nextOffset: Int?) { self.offset = offset; self.returned = returned; self.total = total; self.nextOffset = nextOffset }
}
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
        var segments = Set<PathSegment>()
        for child in children { guard segments.insert(child.segment).inserted else { return false } }
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
private func containerCompleteness(_ children: [InspectChild],offset: Int,end: Int,total: Int) -> Completeness {
    for child in children { if child.node.completeness != .complete { return child.node.completeness } }
    return offset == 0 && end == total ? .complete : .partial(.pagination)
}
private struct ProjectionBudgetFault: Error { let reason: IncompleteReason }
/// Owns bounded projection accounting. Application closures remain cooperative;
/// these limits do not bound their allocations, process RSS or execution time.
public final class InspectContext {
    public let limits: InspectLimits
    public private(set) var work = 0
    public private(set) var nodes = 0
    public private(set) var bytes = 0
    private var depth = 0
    private var budgetScopes: [IncompleteReason?] = []
    private func budgetExceeded(_ reason: IncompleteReason) throws -> Never {
        if !budgetScopes.isEmpty { budgetScopes[budgetScopes.count-1] = reason }
        throw InspectError.budgetExceeded
    }
    private func withinBudgetScope<T>(_ body: () throws -> T) throws -> T {
        budgetScopes.append(nil); defer { budgetScopes.removeLast() }
        let result = try body()
        if let reason = budgetScopes.last! { throw ProjectionBudgetFault(reason:reason) }
        return result
    }
    public init(limits: InspectLimits = InspectLimits()) throws {
        guard limits.maxWork >= 0,limits.maxDepth >= 0,limits.maxNodes >= 0,limits.maxBytes >= 0,limits.maxValueBytes >= 0,limits.maxPageSize >= 0 else { throw InspectError.invalidLimits }
        self.limits = limits
    }
    public func chargeWork(_ amount: Int = 1) throws { guard amount >= 0, amount <= limits.maxWork-work else { try budgetExceeded(.work) }; work += amount }
    private func chargeBytes(_ count: Int) throws { guard count >= 0,count <= limits.maxBytes-bytes else { try budgetExceeded(.bytes) }; bytes += count }
    private func validateMetadata(_ value: String) throws {
        guard value.utf8.count <= 4096,value.utf8.count <= limits.maxBytes-bytes else { throw InspectError.invalidMetadata }
    }
    private func charge(_ payload: Int) throws {
        try chargeWork()
        guard depth <= limits.maxDepth else { try budgetExceeded(.depth) }
        guard nodes < limits.maxNodes else { try budgetExceeded(.nodes) }
        guard payload >= 0, 128 <= limits.maxBytes-bytes, payload <= limits.maxBytes-bytes-128 else { try budgetExceeded(.bytes) }
        nodes += 1; bytes += 128+payload
    }
    public func scalar(_ value: DisplayScalar) throws -> InspectNode {
        try validateDisplayScalar(value)
        let payload: Int
        let kindBytes: Int
        let decimal: String
        switch value {
        case let .integer(_,text): kindBytes = 0; decimal = text.value
        case let .float(kind,text,_): kindBytes = kind.value.utf8.count; decimal = text.value
        default: kindBytes = 0; decimal = ""
        }
        guard 128 <= limits.maxBytes-bytes, kindBytes <= limits.maxBytes-bytes-128 else { try budgetExceeded(.bytes) }
        let available = limits.maxBytes-bytes-128-kindBytes
        let count = decimal.utf8.prefix(available == Int.max ? available : available+1).count
        guard count <= available else { try budgetExceeded(.bytes) }
        payload = kindBytes + count
        try charge(payload); return InspectNode(kind: .scalar(value),childCount:0)
    }
    public func string(_ value: String) throws -> InspectNode {
        guard 128 <= limits.maxBytes-bytes else { try budgetExceeded(.bytes) }
        let maximum = min(limits.maxValueBytes,limits.maxBytes-bytes-128)
        var preview = "", count = 0
        for scalar in value.unicodeScalars { let text = String(scalar); guard text.utf8.count <= maximum-count else { break }; preview += text; count += text.utf8.count }
        try charge(count)
        return InspectNode(kind: .string(preview: ExactString(preview),totalBytes: value.utf8.count),childCount:0,completeness: count == value.utf8.count ? .complete : .partial(.valueLimit))
    }
    public func bytes(_ value: [UInt8]) throws -> InspectNode {
        guard 128 <= limits.maxBytes-bytes else { try budgetExceeded(.bytes) }
        let count = min(value.count,limits.maxValueBytes,limits.maxBytes-bytes-128)
        try charge(count)
        return InspectNode(kind: .bytes(preview: Array(value.prefix(count)),totalBytes: value.count),childCount:0,completeness: count == value.count ? .complete : .partial(.valueLimit))
    }
    /// Redaction skips evaluation entirely, including a closure that throws.
    public func field(_ name: String, redacted: Bool = false,source: SourceSite? = nil, project: (InspectContext) throws -> InspectNode) throws -> InspectChild {
        try field(id:name,label:name,redacted:redacted,source:source,project:project)
    }
    /// The stable path identity and the display label may differ.
    public func field(id: String,label: String,redacted: Bool = false,source: SourceSite? = nil,project: (InspectContext) throws -> InspectNode) throws -> InspectChild {
        try validateMetadata(id); try validateMetadata(label)
        try chargeWork()
        try chargeBytes(64)
        if let source {
            guard source.file.utf8.count <= limits.maxValueBytes else { try budgetExceeded(.valueLimit) }
            try chargeBytes(source.file.utf8.count)
        }
        try chargeBytes(id.utf8.count); try chargeBytes(label.utf8.count)
        guard depth < limits.maxDepth else { try budgetExceeded(.depth) }
        if redacted { try charge(0); return InspectChild(segment: .field(ExactString(id)),label: ExactString(label),node: InspectNode(kind: .redacted,childCount:0,completeness: .partial(.redacted)),source: source) }
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        return try InspectChild(segment: .field(ExactString(id)),label: ExactString(label),node: project(self),source: source)
    }
    public func object(_ name: String, fields: (InspectContext) throws -> [InspectChild]) throws -> InspectNode {
        try validateMetadata(name)
        try charge(name.utf8.count)
        let children = try fields(self)
        return InspectNode(kind: .object(ExactString(name)),children: children,childCount: children.count,completeness:containerCompleteness(children,offset:0,end:children.count,total:children.count))
    }
    /// Project only the requested contiguous field page, without invoking omitted fields.
    public func object(_ name: String,total: Int,page: PageRequest,project: (Int,InspectContext) throws -> InspectChild) throws -> InspectNode {
        guard total >= 0,page.offset >= 0,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        try validateMetadata(name)
        try charge(name.utf8.count)
        let offset = min(page.offset,total), end = offset + min(page.limit,total-offset)
        if depth >= limits.maxDepth && total > 0 {
            return InspectNode(kind:.object(ExactString(name)),childCount:total,page:PageInfo(offset:offset,returned:0,total:total,nextOffset:offset),completeness:.partial(.depth))
        }
        var children: [InspectChild] = [], partial: IncompleteReason?
        for index in offset..<end {
            let oldNodes = nodes, oldBytes = bytes
            do { children.append(try withinBudgetScope { try project(index,self) }) }
            catch let fault as ProjectionBudgetFault {
                nodes = oldNodes; bytes = oldBytes; partial = fault.reason; break
            }
        }
        let after = offset + children.count
        let info = PageInfo(offset:offset,returned:children.count,total:total,nextOffset:after < total ? after : nil)
        return InspectNode(kind:.object(ExactString(name)),children:children,childCount:total,page:info,completeness:partial.map { .partial($0) } ?? containerCompleteness(children,offset:offset,end:after,total:total))
    }
    public func sequence(name: String, total: Int, page: PageRequest = PageRequest(), project: (Int,InspectContext) throws -> InspectNode) throws -> InspectNode {
        guard total >= 0,page.offset >= 0,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        try validateMetadata(name)
        try charge(name.utf8.count)
        let offset = min(page.offset,total), end = offset + min(page.limit,total-offset)
        if depth >= limits.maxDepth && total > 0 {
            return InspectNode(kind:.sequence(ExactString(name)),childCount:total,page:PageInfo(offset:offset,returned:0,total:total,nextOffset:offset),completeness:.partial(.depth))
        }
        var children: [InspectChild] = [], partial: IncompleteReason?
        if offset < end {
            depth += 1; defer { depth -= 1 }
            for i in offset..<end {
                let oldNodes = nodes, oldBytes = bytes
                do {
                    let child = try withinBudgetScope { try chargeWork(); try chargeBytes(64); try chargeBytes(16); try chargeBytes(String(i).utf8.count); return try InspectChild(segment: .index(UInt64(i)),label: ExactString(String(i)),node: project(i,self)) }
                    children.append(child)
                }
                catch let fault as ProjectionBudgetFault {
                    nodes = oldNodes; bytes = oldBytes; partial = fault.reason; break
                }
            }
        }
        let after = offset + children.count
        let info = PageInfo(offset: offset,returned: children.count,total: total,nextOffset: after < total ? after : nil)
        return InspectNode(kind: .sequence(ExactString(name)),children: children,childCount: total,page: info,completeness:partial.map { .partial($0) } ?? containerCompleteness(children,offset:offset,end:after,total:total))
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

public enum InspectMapKey: Hashable {
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
        var preview = "", count = 0
        for scalar in label.unicodeScalars {
            let text = String(scalar)
            guard text.utf8.count <= limits.maxValueBytes-count else { break }
            preview += text; count += text.utf8.count
        }
        try charge(count)
        return InspectNode(kind: .opaque(ExactString(preview)),childCount:0,completeness: .partial(.opaque))
    }
    public func unavailable() throws -> InspectNode { try charge(0); return InspectNode(kind: .unavailable,childCount:0,completeness: .partial(.unavailable)) }
    public func enumeration(name: String,variantID: String,variantLabel: String,fields: (InspectContext) throws -> [InspectChild]) throws -> InspectNode {
        for value in [name,variantID,variantLabel] { try validateMetadata(value) }
        try charge(name.utf8.count+variantID.utf8.count+variantLabel.utf8.count)
        if depth >= limits.maxDepth {
            return InspectNode(kind:.enumeration(name:ExactString(name),variantID:ExactString(variantID),variantLabel:ExactString(variantLabel)),childCount:1,page:PageInfo(offset:0,returned:0,total:1,nextOffset:0),completeness:.partial(.depth))
        }
        try chargeBytes(64); try chargeBytes(variantID.utf8.count); try chargeBytes(variantLabel.utf8.count)
        depth += 1; defer { depth -= 1 }
        guard depth <= limits.maxDepth else { throw InspectError.budgetExceeded }
        let object = try self.object(variantLabel,fields: fields)
        let child = InspectChild(segment: .variant(ExactString(variantID)),label: ExactString(variantLabel),node: object)
        return InspectNode(kind: .enumeration(name: ExactString(name),variantID: ExactString(variantID),variantLabel: ExactString(variantLabel)),children: [child],childCount: 1,completeness:object.completeness)
    }
    /// Pages the variant and then its fields, without evaluating omitted fields.
    public func enumeration(name: String,variantID: String,variantLabel: String,fieldCount: Int,page: PageRequest,project: (Int,InspectContext) throws -> InspectChild) throws -> InspectNode {
        guard fieldCount >= 0,page.offset >= 0,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        for value in [name,variantID,variantLabel] { try validateMetadata(value) }
        try charge(name.utf8.count+variantID.utf8.count+variantLabel.utf8.count)
        let offset = min(page.offset,1), kind = NodeKind.enumeration(name:ExactString(name),variantID:ExactString(variantID),variantLabel:ExactString(variantLabel))
        if depth >= limits.maxDepth { return InspectNode(kind:kind,childCount:1,page:PageInfo(offset:offset,returned:0,total:1,nextOffset:offset),completeness:.partial(.depth)) }
        guard offset == 0 else { return InspectNode(kind:kind,childCount:1,page:PageInfo(offset:1,returned:0,total:1,nextOffset:nil),completeness:.partial(.pagination)) }
        let oldNodes = nodes, oldBytes = bytes
        let object: InspectNode
        do {
            object = try withinBudgetScope {
                try chargeBytes(64); try chargeBytes(variantID.utf8.count); try chargeBytes(variantLabel.utf8.count)
                depth += 1; defer { depth -= 1 }
                return try self.object(variantLabel,total:fieldCount,page:PageRequest(offset:0,limit:page.limit),project:project)
            }
        } catch let fault as ProjectionBudgetFault {
            nodes = oldNodes; bytes = oldBytes
            return InspectNode(kind:kind,childCount:1,page:PageInfo(offset:0,returned:0,total:1,nextOffset:0),completeness:.partial(fault.reason))
        }
        let child = InspectChild(segment:.variant(ExactString(variantID)),label:ExactString(variantLabel),node:object)
        return InspectNode(kind:kind,children:[child],childCount:1,page:PageInfo(offset:0,returned:1,total:1,nextOffset:nil),completeness:object.completeness)
    }
    /// Iterator work and payload projection are bounded independently. Stable
    /// ordering is a host declaration; unstable maps always compare Unknown.
    public func map(ordering: MapOrdering,total: Int,page: PageRequest = PageRequest(),next: () throws -> InspectMapEntry?) throws -> InspectNode {
        guard total >= 0,page.offset >= 0,page.limit > 0,page.limit <= limits.maxPageSize else { throw InspectError.invalidPage }
        try charge(0)
        let offset = min(page.offset,total), end = offset+min(page.limit,total-offset)
        if depth >= limits.maxDepth && total > 0 {
            return InspectNode(kind:.map(ordering),childCount:total,page:PageInfo(offset:offset,returned:0,total:total,nextOffset:offset),completeness:.partial(.depth))
        }
        var children: [InspectChild] = [], partial: IncompleteReason?
        if offset < end {
          depth += 1; defer { depth -= 1 }
          for i in 0..<end {
            let oldNodes = nodes, oldBytes = bytes
            do {
            let child: InspectChild? = try withinBudgetScope {
            try chargeWork(); guard let entry = try next() else { throw InspectError.pathNotFound }
            if i < offset { return nil }
            try chargeBytes(64)
            try validateMapKey(entry.key)
            let keyBytes: Int
            switch entry.key {
            case let .string(value): keyBytes = value.value.utf8.count
            case let .integer(kind,decimal): keyBytes = kind.value.utf8.count + decimal.value.utf8.count
            case .bool,.character: keyBytes = 16
            }
            guard keyBytes <= limits.maxValueBytes else { throw InspectError.budgetExceeded }
            try chargeBytes(keyBytes)
            guard entry.label.utf8.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }
            try chargeBytes(entry.label.utf8.count)
            return try InspectChild(segment: entry.key.segment,label: ExactString(entry.label),node: entry.project(self))
            }
            if let child { children.append(child) }
            } catch let fault as ProjectionBudgetFault {
                nodes = oldNodes; bytes = oldBytes; partial = fault.reason; break
            }
          }
        }
        let after = offset + children.count
        return InspectNode(kind: .map(ordering),children: children,childCount: total,page: PageInfo(offset: offset,returned: children.count,total: total,nextOffset: after < total ? after : nil),completeness:partial.map { .partial($0) } ?? containerCompleteness(children,offset:offset,end:after,total:total))
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
            for index in node.children.indices {
                guard consume() else { return false }
                for prior in node.children.indices.prefix(index) {
                    guard consume() else { return false }
                    if node.children[prior].segment == node.children[index].segment { return false }
                }
                stack.append(node.children[index].node)
            }
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
        if a.childCount != b.childCount || a.page != b.page { add(path,.changed) }
        if a.children.count == b.children.count {
            for (left,right) in zip(a.children,b.children) {
                guard consume() else { return }
                if left.segment != right.segment { add(path,.changed); break }
            }
        }
        for child in a.children {
            var selected: InspectChild?
            for candidate in b.children { guard consume() else { return }; if candidate.segment == child.segment { selected = candidate; break } }
            let childPath = path+[child.segment]
            if let selected,child.label != selected.label || child.source != selected.source { add(childPath,.changed) }
            visit(child.node,selected?.node,childPath)
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
        // A path is usable only when exactly one sibling names it. Scanning
        // the whole sibling set spends the caller's explicit work budget.
        for child in node.children {
            guard work < maxWork else { throw InspectError.budgetExceeded }; work += 1
            if child.segment == segment {
                guard selected == nil else { throw InspectError.invalidMetadata }
                selected = child.node
            }
        }
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
    func metadata(_ value: String) throws { guard value.utf8.count <= 4096,value.utf8.count <= limits.maxBytes-bytes else { throw InspectError.invalidMetadata }; try charge(value.utf8.count) }
    func segment(_ value: PathSegment) throws {
        switch value {
        case let .field(s),let .variant(s): try metadata(s.value)
        case let .mapKey(s): try text(s.value)
        case let .typedMapKey(key): try validateMapKey(key); switch key { case let .string(s): try text(s.value); case let .integer(k,d): try text(k.value); try text(d.value); default: try charge(16) }
        default: try charge(16)
        }
    }
    while let (node,depth) = stack.popLast() {
        guard nodes < limits.maxNodes,work < limits.maxWork,depth <= limits.maxDepth else { throw InspectError.budgetExceeded }; nodes += 1; work += 1; try charge(128)
        // Rust's corresponding fields are unsigned; reject values Swift's
        // public Int-based constructors can represent but the wire cannot.
        if let count = node.childCount, count < 0 { throw InspectError.invalidMetadata }
        if let page = node.page {
            guard page.offset >= 0,page.returned >= 0,page.total >= 0,page.nextOffset.map({ $0 >= 0 }) ?? true else { throw InspectError.invalidMetadata }
        }
        switch node.kind {
        case let .scalar(value):
            try validateDisplayScalar(value)
            let decimal: String
            switch value { case let .integer(_,d): decimal = d.value; case let .float(k,d,_): try charge(k.value.utf8.count); decimal = d.value; default: decimal = "" }
            let available = limits.maxBytes-bytes
            let count = decimal.utf8.prefix(available == Int.max ? available : available+1).count
            guard count <= available else { throw InspectError.budgetExceeded }
            try charge(count)
        case let .string(s,total): guard total >= 0 else { throw InspectError.invalidMetadata }; try text(s.value)
        case let .bytes(b,total): guard total >= 0 else { throw InspectError.invalidMetadata }; guard b.count <= limits.maxValueBytes else { throw InspectError.budgetExceeded }; try charge(b.count)
        case let .object(s),let .sequence(s): try metadata(s.value)
        case let .opaque(s): try text(s.value)
        case let .enumeration(n,i,l): try metadata(n.value); try metadata(i.value); try metadata(l.value)
        default: break
        }
        guard node.children.count <= limits.maxNodes-nodes, node.children.count <= limits.maxWork-work else { throw InspectError.budgetExceeded }
        for child in node.children { work += 1; try charge(64); try metadata(child.label.value); if let source = child.source { try text(source.file) }; try segment(child.segment); stack.append((child.node,depth+1)) }
    }
}

/// Swift owns float kind text, unlike Rust's static kind string, so bound it.
private func validCharacter(_ scalar: UInt32) -> Bool { scalar <= 0x10FFFF && !(0xD800...0xDFFF).contains(scalar) }
private func validateDisplayScalar(_ value: DisplayScalar) throws {
    switch value {
    case let .integer(kind,_):
        let kinds = ["i8","i16","i32","i64","i128","isize","u8","u16","u32","u64","u128","usize"]
        guard kinds.contains(where:{ exactText($0,kind.value) }) else { throw InspectError.invalidMetadata }
    case let .character(scalar): guard validCharacter(scalar) else { throw InspectError.invalidMetadata }
    case let .float(kind,_,_):
        guard kind.value.utf8.prefix(4097).count <= 4096 else { throw InspectError.invalidMetadata }
    default: break
    }
}
private func validateMapKey(_ value: InspectMapKey) throws {
    switch value {
    // Rust's MapKey stores a typed kind and an arbitrary decimal String;
    // the text is an identity, not a parsed value.
    case let .integer(kind,_):
        let kinds = ["i8","i16","i32","i64","i128","isize","u8","u16","u32","u64","u128","usize"]
        guard kinds.contains(where:{ exactText($0,kind.value) }) else { throw InspectError.invalidMetadata }
    case let .character(scalar): guard validCharacter(scalar) else { throw InspectError.invalidMetadata }
    default: break
    }
}
