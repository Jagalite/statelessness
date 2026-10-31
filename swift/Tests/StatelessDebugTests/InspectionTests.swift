import XCTest
import Statelessness
@testable import StatelessDebug

final class InspectionTests: XCTestCase {
    func testCaughtSourceBudgetFailureMarksPageIncomplete() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 2
        let context = try InspectContext(limits:limits)
        let page = try context.object("R",total:1,page:PageRequest(offset:0,limit:1)) { _, c in
            do {
                return try c.field("x",source:SourceSite(file:"long",line:1,column:1)) { try $0.scalar(.bool(true)) }
            } catch InspectError.budgetExceeded {
                return InspectChild(segment:.field(ExactString("x")),label:ExactString("x"),node:InspectNode(kind:.scalar(.bool(true)),childCount:0))
            }
        }
        XCTAssertEqual(page.completeness,.partial(.valueLimit))
        XCTAssertEqual(page.children.count,0)
        XCTAssertEqual(page.page,PageInfo(offset:0,returned:0,total:1,nextOffset:0))
        limits.maxValueBytes = 8; limits.maxBytes = 195
        let bytesContext = try InspectContext(limits:limits)
        let bytesPage = try bytesContext.object("R",total:1,page:PageRequest(offset:0,limit:1)) { _, c in
            do {
                return try c.field("x",source:SourceSite(file:"long",line:1,column:1)) { try $0.scalar(.bool(true)) }
            } catch InspectError.budgetExceeded {
                return InspectChild(segment:.field(ExactString("x")),label:ExactString("x"),node:InspectNode(kind:.scalar(.bool(true)),childCount:0))
            }
        }
        XCTAssertEqual(bytesPage.completeness,.partial(.bytes))
        XCTAssertEqual(bytesPage.children.count,0)
        XCTAssertEqual(bytesPage.page,PageInfo(offset:0,returned:0,total:1,nextOffset:0))
    }
    func testMapKeysPreserveArbitraryRustIntegerDecimalIdentity() throws {
        let key = InspectMapKey.integer(kind:ExactString("u8"),decimal:ExactString("256"))
        let child = InspectChild(segment:.typedMapKey(key),label:ExactString("key"),node:InspectNode(kind:.scalar(.bool(true)),childCount:0))
        let custom = InspectNode(kind:.map(.stable),children:[child],childCount:1)
        XCTAssertNoThrow(try validateProjection(custom,limits:InspectLimits()))
        let context = try InspectContext()
        var emitted = false
        let node = try context.map(ordering:.stable,total:1) {
            guard !emitted else { return nil }; emitted = true
            return InspectMapEntry(key:key,label:"key") { try $0.scalar(.bool(true)) }
        }
        XCTAssertEqual(node.children.first?.segment,.typedMapKey(key))
    }
    func testExactScalarsDoNotUsePreviewByteLimit() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 0
        let value = DisplayScalar.integer(kind:ExactString("u128"),decimal:ExactString("340282366920938463463374607431768211455"))
        let context = try InspectContext(limits:limits)
        let node = try context.scalar(value)
        XCTAssertEqual(node.kind,.scalar(value)); XCTAssertEqual(node.childCount,0)
        XCTAssertNoThrow(try validateProjection(node,limits:limits))
        XCTAssertNoThrow(try InspectContext(limits:limits).scalar(.bool(true)))
        limits.maxBytes = 128
        XCTAssertThrowsError(try InspectContext(limits:limits).scalar(value))
    }
    func testOpaqueLabelIsBoundedAtUnicodeScalarBoundary() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 3
        let node = try InspectContext(limits:limits).opaque("é猫tail")
        XCTAssertEqual(node.kind,.opaque(ExactString("é"))); XCTAssertEqual(node.completeness,.partial(.opaque))
        XCTAssertEqual(node.childCount,0); XCTAssertNoThrow(try validateProjection(node,limits:limits))
        limits.maxValueBytes = 0
        XCTAssertEqual(try InspectContext(limits:limits).opaque("é").kind,.opaque(ExactString("")))
    }
    func testFieldIdentityIsIndependentFromItsDisplayLabel() throws {
        let context = try InspectContext()
        let node = try context.object("record") { c in
            [try c.field(id:"stable-id",label:"Friendly label") { try $0.scalar(.bool(true)) }]
        }
        XCTAssertEqual(node.children[0].segment,.field(ExactString("stable-id")))
        XCTAssertEqual(node.children[0].label,ExactString("Friendly label"))
        let result = InspectResult(snapshot:SnapshotID(session:1,revision:0,sequence:0),schema:DisplaySchema(name:"record"),node:node)
        XCTAssertEqual(try resolve(result,path:[.field(ExactString("stable-id"))],schemaVersion:1).node.kind,.scalar(.bool(true)))
        XCTAssertThrowsError(try resolve(result,path:[.field(ExactString("Friendly label"))],schemaVersion:1))
        var evaluated = false
        let redacted = try InspectContext().field(id:"secret",label:"Private",redacted:true) { _ in evaluated = true; throw InspectError.invalidMetadata }
        XCTAssertFalse(evaluated); XCTAssertEqual(redacted.segment,.field(ExactString("secret"))); XCTAssertEqual(redacted.label,ExactString("Private"))
    }
    func testFieldMetadataUsesIndependentLimitFromValuePreview() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 32
        let id = String(repeating:"i",count:64), label = String(repeating:"l",count:80)
        let context = try InspectContext(limits:limits)
        let node = try context.object("record") { c in [try c.field(id:id,label:label) { try $0.scalar(.bool(true)) }] }
        XCTAssertEqual(node.children[0].segment,.field(ExactString(id))); XCTAssertEqual(node.children[0].label,ExactString(label))
        XCTAssertNoThrow(try validateProjection(node,limits:limits))
        var called = false
        XCTAssertThrowsError(try InspectContext().field(id:String(repeating:"x",count:4097),label:"ok") { _ in called = true; throw InspectError.invalidMetadata }) { error in guard case InspectError.invalidMetadata = error else { return XCTFail("expected invalid metadata") } }
        XCTAssertFalse(called)
        limits.maxBytes = 20
        XCTAssertThrowsError(try InspectContext(limits:limits).field(id:String(repeating:"x",count:21),label:"ok") { _ in called = true; throw InspectError.invalidMetadata }) { error in guard case InspectError.invalidMetadata = error else { return XCTFail("metadata must be checked before child overhead") } }
        XCTAssertFalse(called)
        limits.maxWork = 0
        XCTAssertThrowsError(try InspectContext(limits:limits).field(id:String(repeating:"x",count:21),label:"ok") { _ in called = true; throw InspectError.invalidMetadata }) { error in guard case InspectError.invalidMetadata = error else { return XCTFail("metadata must be checked before work admission") } }
        XCTAssertFalse(called)
    }
    func testContainerMetadataIsIndependentOfValuePreviewLimit() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 16
        let name = String(repeating:"n",count:64), label = String(repeating:"l",count:64)
        let object = try InspectContext(limits:limits).object(name) { _ in [] }
        let sequence = try InspectContext(limits:limits).sequence(name:name,total:0,page:PageRequest(offset:0,limit:1)) { _,_ in throw InspectError.invalidMetadata }
        let enumeration = try InspectContext(limits:limits).enumeration(name:name,variantID:name,variantLabel:label,fieldCount:0,page:PageRequest(offset:0,limit:1)) { _,_ in throw InspectError.invalidMetadata }
        for node in [object,sequence,enumeration] { XCTAssertNoThrow(try validateProjection(node,limits:limits)) }
        XCTAssertThrowsError(try InspectContext(limits:limits).object(String(repeating:"x",count:4097)) { _ in [] }) { error in guard case InspectError.invalidMetadata = error else { return XCTFail("expected invalid metadata") } }
    }
    func testProjectionRejectsNegativeUnsignedWireMetadata() throws {
        let invalid: [InspectNode] = [
            InspectNode(kind:.scalar(.bool(true)),childCount:-1),
            InspectNode(kind:.string(preview:ExactString(""),totalBytes:-1)),
            InspectNode(kind:.bytes(preview:[],totalBytes:-1)),
            InspectNode(kind:.sequence(ExactString("items")),page:PageInfo(offset:-1,returned:0,total:0,nextOffset:nil)),
            InspectNode(kind:.sequence(ExactString("items")),page:PageInfo(offset:0,returned:-1,total:0,nextOffset:nil)),
            InspectNode(kind:.sequence(ExactString("items")),page:PageInfo(offset:0,returned:0,total:-1,nextOffset:nil)),
            InspectNode(kind:.sequence(ExactString("items")),page:PageInfo(offset:0,returned:0,total:0,nextOffset:-1))
        ]
        for node in invalid {
            XCTAssertThrowsError(try validateProjection(node,limits:InspectLimits())) { error in
                guard case InspectError.invalidMetadata = error else { return XCTFail("expected invalid metadata") }
            }
        }
    }
    func testUncaughtEngineBudgetErrorPropagatesWithoutClaimingPartialPage() throws {
        var limits = InspectLimits(); limits.maxNodes = 1
        let context = try InspectContext(limits:limits)
        XCTAssertThrowsError(try context.sequence(name:"items",total:1,page:PageRequest(offset:0,limit:1)) { _,c in
            try c.scalar(.bool(true))
        }) { error in guard case InspectError.budgetExceeded = error else { return XCTFail("expected typed engine budget error") } }
    }
    func testSequenceAndMapReturnBoundedPrefixOnEngineBudgetExhaustion() throws {
        var limits = InspectLimits(); limits.maxNodes = 2
        let page = PageRequest(offset:0,limit:2)
        let sequenceContext = try InspectContext(limits:limits)
        let sequence = try sequenceContext.sequence(name:"items",total:2,page:page) { _,c in
            do { return try c.scalar(.bool(true)) } catch InspectError.budgetExceeded { return InspectNode(kind:.scalar(.bool(true)),childCount:0) }
        }
        XCTAssertEqual(sequence.completeness,.partial(.nodes)); XCTAssertEqual(sequence.page,PageInfo(offset:0,returned:1,total:2,nextOffset:1)); XCTAssertEqual(sequenceContext.nodes,2)
        var index = 0
        let mapContext = try InspectContext(limits:limits)
        let map = try mapContext.map(ordering:.stable,total:2,page:page) {
            defer { index += 1 }; return InspectMapEntry(key:.integer(kind:ExactString("u8"),decimal:ExactString(String(index))),label:String(index)) { c in
                do { return try c.scalar(.bool(true)) } catch InspectError.budgetExceeded { return InspectNode(kind:.scalar(.bool(true)),childCount:0) }
            }
        }
        XCTAssertEqual(map.completeness,.partial(.nodes)); XCTAssertEqual(map.page,PageInfo(offset:0,returned:1,total:2,nextOffset:1)); XCTAssertEqual(mapContext.nodes,2)
        let host = try InspectContext()
        XCTAssertThrowsError(try host.sequence(name:"items",total:1,page:PageRequest(offset:0,limit:1)) { _,_ in throw InspectError.budgetExceeded })
        let hostMap = try InspectContext()
        XCTAssertThrowsError(try hostMap.map(ordering:.stable,total:1,page:PageRequest(offset:0,limit:1)) { throw InspectError.budgetExceeded })
    }
    func testCaughtEngineLimitCannotMisclassifyHostErrorOrCertifySuccess() throws {
        var limits = InspectLimits(); limits.maxWork = 2
        let page = PageRequest(offset:0,limit:1)
        let hostError = try InspectContext(limits:limits)
        XCTAssertThrowsError(try hostError.sequence(name:"items",total:1,page:page) { _,c in
            do { try c.chargeWork() } catch InspectError.budgetExceeded { }
            throw InspectError.budgetExceeded
        }) { error in guard case InspectError.budgetExceeded = error else { return XCTFail("host error was replaced") } }
        let swallowed = try InspectContext(limits:limits)
        let partial = try swallowed.sequence(name:"items",total:1,page:page) { _,c in
            do { try c.chargeWork() } catch InspectError.budgetExceeded { }
            return InspectNode(kind:.scalar(.bool(true)),childCount:0)
        }
        XCTAssertEqual(partial.completeness,.partial(.work)); XCTAssertEqual(partial.children.count,0)
    }
    func testEnumPagingProjectsOnlySelectedVariantAndFieldPrefix() throws {
        var visited: [Int] = []
        let node = try InspectContext().enumeration(name:"Choice",variantID:"item",variantLabel:"Item",fieldCount:3,page:PageRequest(offset:0,limit:1)) { index,context in
            visited.append(index); return try context.field("field\(index)") { try $0.scalar(.bool(true)) }
        }
        XCTAssertEqual(visited,[0]); XCTAssertEqual(node.page,PageInfo(offset:0,returned:1,total:1,nextOffset:nil))
        XCTAssertEqual(node.children[0].node.page,PageInfo(offset:0,returned:1,total:3,nextOffset:1))
        XCTAssertEqual(node.completeness,.partial(.pagination))
        let past = try InspectContext().enumeration(name:"Choice",variantID:"item",variantLabel:"Item",fieldCount:3,page:PageRequest(offset:9,limit:1)) { _,_ in XCTFail("unexpected enum field"); throw InspectError.invalidMetadata }
        XCTAssertEqual(past.page,PageInfo(offset:1,returned:0,total:1,nextOffset:nil)); XCTAssertEqual(past.completeness,.partial(.pagination))
    }
    func testPagedEnumRollsBackFailedChildAndReportsBudgetReason() throws {
        let page = PageRequest(offset:0,limit:1)
        var limits = InspectLimits(); limits.maxNodes = 2
        let nodes = try InspectContext(limits:limits)
        let result = try nodes.enumeration(name:"E",variantID:"v",variantLabel:"V",fieldCount:1,page:page) { _,c in
            do { return try c.field("x") { try $0.scalar(.bool(true)) } } catch InspectError.budgetExceeded { return InspectChild(segment:.field(ExactString("x")),label:ExactString("x"),node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) }
        }
        XCTAssertEqual(result.completeness,.partial(.nodes)); XCTAssertEqual(result.children.count,1)
        XCTAssertEqual(result.children[0].node.page,PageInfo(offset:0,returned:0,total:1,nextOffset:0)); XCTAssertEqual(nodes.nodes,2)
        limits.maxNodes = 100; limits.maxWork = 3
        let work = try InspectContext(limits:limits)
        let workResult = try work.enumeration(name:"E",variantID:"v",variantLabel:"V",fieldCount:1,page:page) { _,c in
            do { return try c.field("x") { try $0.scalar(.bool(true)) } } catch InspectError.budgetExceeded { return InspectChild(segment:.field(ExactString("x")),label:ExactString("x"),node:InspectNode(kind:.scalar(.bool(true)),childCount:0)) }
        }
        XCTAssertEqual(workResult.completeness,.partial(.work)); XCTAssertEqual(work.nodes,2); XCTAssertEqual(work.work,3)
        limits.maxWork = 100; limits.maxBytes = 140
        let bytes = try InspectContext(limits:limits)
        XCTAssertThrowsError(try bytes.enumeration(name:"E",variantID:"v",variantLabel:"V",fieldCount:1,page:page) { _,c in try c.field("x") { try $0.scalar(.bool(true)) } }) { error in guard case InspectError.budgetExceeded = error else { return XCTFail("expected typed budget error") } }
    }
    func testPartialChildCompletenessPropagatesThroughContainers() throws {
        var limits = InspectLimits(); limits.maxDepth = 1
        let page = PageRequest(offset:0,limit:1)
        func nested(_ context: InspectContext) throws -> InspectNode {
            try context.sequence(name:"inner",total:1,page:page) { _,_ in XCTFail("unexpected nested projection"); throw InspectError.invalidMetadata }
        }
        let object = try InspectContext(limits:limits).object("outer",total:1,page:page) { _,context in try context.field("item") { try nested($0) } }
        let sequence = try InspectContext(limits:limits).sequence(name:"outer",total:1,page:page) { _,context in try nested(context) }
        let map = try InspectContext(limits:limits).map(ordering:.stable,total:1,page:page) { InspectMapEntry(key:.string(ExactString("key")),label:"key",project:nested) }
        for node in [object,sequence,map] { XCTAssertEqual(node.completeness,.partial(.depth)); XCTAssertEqual(node.children.first?.node.completeness,.partial(.depth)) }
        let paged = try InspectContext(limits:limits).sequence(name:"outer",total:2,page:page) { _,context in try nested(context) }
        XCTAssertEqual(paged.completeness,.partial(.depth)) // Child reason takes precedence over pagination.
    }
    func testZeroDepthReturnsBoundedContainerInsteadOfProjectingChildren() throws {
        var limits = InspectLimits(); limits.maxDepth = 0
        let page = PageRequest(offset:0,limit:1)
        let object = try InspectContext(limits:limits).object("record",total:1,page:page) { _,_ in XCTFail("unexpected object projection"); throw InspectError.invalidMetadata }
        let sequence = try InspectContext(limits:limits).sequence(name:"items",total:1,page:page) { _,_ in XCTFail("unexpected sequence projection"); throw InspectError.invalidMetadata }
        let map = try InspectContext(limits:limits).map(ordering:.stable,total:1,page:page) { XCTFail("unexpected map iteration"); throw InspectError.invalidMetadata }
        let enumeration = try InspectContext(limits:limits).enumeration(name:"Choice",variantID:"item",variantLabel:"Item") { _ in XCTFail("unexpected enum projection"); throw InspectError.invalidMetadata }
        for node in [object,sequence,map,enumeration] { XCTAssertEqual(node.completeness,.partial(.depth)); XCTAssertEqual(node.page,PageInfo(offset:0,returned:0,total:1,nextOffset:0)) }
        let empty = try InspectContext(limits:limits).sequence(name:"items",total:0,page:page) { _,_ in XCTFail("unexpected sequence projection"); throw InspectError.invalidMetadata }
        XCTAssertTrue(empty.isComplete); XCTAssertEqual(empty.page,PageInfo(offset:0,returned:0,total:0,nextOffset:nil))
        let emptyMap = try InspectContext(limits:limits).map(ordering:.stable,total:0,page:page) { XCTFail("unexpected map iteration"); throw InspectError.invalidMetadata }
        XCTAssertTrue(emptyMap.isComplete)
    }
    func testPastEndPageClampsWithoutProjectingFieldsOrMapEntries() throws {
        let page = PageRequest(offset:9,limit:1)
        let object = try InspectContext().object("record",total:2,page:page) { _,_ in XCTFail("unexpected object projection"); throw InspectError.invalidMetadata }
        XCTAssertEqual(object.page,PageInfo(offset:2,returned:0,total:2,nextOffset:nil))
        XCTAssertEqual(object.completeness,.partial(.pagination))
        let sequence = try InspectContext().sequence(name:"items",total:2,page:page) { _,_ in XCTFail("unexpected sequence projection"); throw InspectError.invalidMetadata }
        XCTAssertEqual(sequence.page,PageInfo(offset:2,returned:0,total:2,nextOffset:nil))
        let map = try InspectContext().map(ordering:.stable,total:2,page:page) { XCTFail("unexpected map iteration"); throw InspectError.invalidMetadata }
        XCTAssertEqual(map.page,PageInfo(offset:2,returned:0,total:2,nextOffset:nil))
    }
    func testBuilderAccountsForStructuralBytesBeforeProjection() throws {
        var limits = InspectLimits(); limits.maxBytes = 127
        XCTAssertThrowsError(try InspectContext(limits:limits).string(""))
        limits.maxBytes = 129
        let context = try InspectContext(limits:limits)
        let preview = try context.string("é")
        XCTAssertEqual(preview.kind,.string(preview:ExactString(""),totalBytes:2))
        XCTAssertEqual(context.bytes,128); XCTAssertFalse(preview.isComplete)
        XCTAssertNoThrow(try validateProjection(preview,limits:limits))
        limits.maxBytes = 192
        var called = false
        XCTAssertThrowsError(try InspectContext(limits:limits).object("x") { context in
            [try context.field("a") { context in called = true; return try context.scalar(.bool(true)) }]
        })
        XCTAssertFalse(called)
        limits.maxBytes = 4096
        let full = try InspectContext(limits:limits).object("x") { context in [try context.field("a") { try $0.scalar(.bool(true)) }] }
        XCTAssertNoThrow(try validateProjection(full,limits:limits))
        limits.maxDepth = 0
        XCTAssertThrowsError(try InspectContext(limits:limits).object("x") { context in [try context.field("a",redacted:true) { _ in XCTFail("redacted callback"); throw ModelError("unexpected") }] })
    }
    func testRedactionSkipsClosureAndPartialDiffStaysUnknown() throws {
        let context = try InspectContext()
        var called = false
        let object = try context.object("Account") { c in
            [try c.field("secret",redacted: true) { _ in called = true; throw ModelError("must never evaluate") }]
        }
        XCTAssertFalse(called); XCTAssertFalse(object.isComplete)
        XCTAssertEqual(compareNodes(object,object),.unknown)
        let full = try InspectContext().scalar(.integer(kind: ExactString("u64"),decimal: ExactString(String(UInt64.max))))
        XCTAssertEqual(compareNodes(full,full),.unchanged)
    }
    func testUTF8PagingAndBoundedWork() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 3
        let node = try InspectContext(limits: limits).string("éé")
        XCTAssertEqual(node.kind,.string(preview: ExactString("é"),totalBytes: 4)); XCTAssertFalse(node.isComplete)
        let context = try InspectContext()
        let page = try context.sequence(name: "items",total: 3,page: PageRequest(offset: 1,limit: 1)) { i,c in try c.scalar(.integer(kind: ExactString("usize"),decimal: ExactString(String(i)))) }
        XCTAssertEqual(page.page?.nextOffset,2); XCTAssertEqual(page.children.first?.segment,.index(1)); XCTAssertFalse(page.isComplete)
        limits.maxWork = 0
        XCTAssertThrowsError(try InspectContext(limits: limits).scalar(.bool(true)))
    }
    func testUnstableMapAndSchemaMismatchUnknown() {
        let unstable = InspectNode(kind: .map(.unstable),childCount: 0)
        XCTAssertEqual(compareNodes(unstable,unstable),.unknown)
        let node = InspectNode(kind: .scalar(.bool(true)))
        let snapshot = SnapshotID(session: UInt64.max,revision: UInt64.max,sequence: UInt64.max)
        XCTAssertEqual(diff(InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),node: node),InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x",version: 2),node: node)),.unknown)
    }
    func testScalarIdentityPreservesRustText() throws {
        let context = try InspectContext()
        let values: [DisplayScalar] = [
            .integer(kind: ExactString("u8"), decimal: ExactString("256")),
            .integer(kind: ExactString("i64"), decimal: ExactString("not-a-number")),
            .float(kind: ExactString("custom"), decimal: ExactString("not-a-number"), bits: UInt64.max),
            .float(kind: ExactString("f32"), decimal: ExactString("1"), bits: UInt64.max)
        ]
        for value in values { XCTAssertEqual(try context.scalar(value).kind, .scalar(value)) }
        var small = InspectLimits(); small.maxBytes = 140
        let oversized = DisplayScalar.integer(kind: ExactString("u8"), decimal: ExactString(String(repeating: "é", count: 1000)))
        XCTAssertThrowsError(try InspectContext(limits: small).scalar(oversized)) { guard case InspectError.budgetExceeded = $0 else { return XCTFail("expected budget exceeded") } }
        let custom = InspectNode(kind: .scalar(oversized), childCount: 0)
        XCTAssertThrowsError(try validateProjection(custom, limits: small)) { guard case InspectError.budgetExceeded = $0 else { return XCTFail("expected budget exceeded") } }
    }
    func testTypedRuntimeAdmissionAndStructuralWorkBudget() throws {
        let context = try InspectContext()
        for (kind,text) in [("fake","1")] {
            XCTAssertThrowsError(try context.scalar(.integer(kind: ExactString(kind),decimal: ExactString(text))))
        }
        XCTAssertThrowsError(try context.scalar(.character(0xD800)))
        XCTAssertThrowsError(try context.scalar(.float(kind: ExactString(String(repeating: "x", count: 4097)),decimal: ExactString("1"),bits: 0)))
        XCTAssertNoThrow(try context.scalar(.integer(kind: ExactString("u128"),decimal: ExactString("340282366920938463463374607431768211455"))))
        let node = try InspectContext().object("x") { c in [try c.field("a") { try $0.scalar(.bool(true)) }] }
        let result = InspectResult(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),schema: DisplaySchema(name: "x"),node: node)
        XCTAssertTrue(try structuralDiff(result,result,maxWork: 0).truncated)
        XCTAssertEqual(try structuralDiff(result,result,maxWork: 1).kind,.unknown)
        XCTAssertEqual(try structuralDiff(result,result,maxWork: 100).kind,.unchanged)
    }
    func testStructuralDiffDetectsOrderAndChildMetadata() throws {
        let snapshot = SnapshotID(session:1,revision:1,sequence:1),schema = DisplaySchema(name:"example")
        func child(_ key: String,_ label: String,_ source: SourceSite? = nil) -> InspectChild {
            InspectChild(segment:.field(ExactString(key)),label:ExactString(label),node:InspectNode(kind:.scalar(.bool(true))),source:source)
        }
        func result(_ children: [InspectChild],page: PageInfo? = nil) -> InspectResult {
            InspectResult(snapshot:snapshot,schema:schema,node:InspectNode(kind:.object(ExactString("x")),children:children,childCount:children.count,page:page))
        }
        let before = result([child("a","A"),child("b","B")])
        for after in [result([child("b","B"),child("a","A")]),result([child("a","changed"),child("b","B")]),result([child("a","A",SourceSite(file:"host.swift",line:1,column:1)),child("b","B")]),result([child("a","A"),child("b","B")],page:PageInfo(offset:0,returned:2,total:2,nextOffset:nil))] {
            XCTAssertEqual(compareNodes(before.node,after.node),.changed)
            XCTAssertEqual(try structuralDiff(before,after).kind,.changed)
        }
        let ambiguous = result([child("a","A"),child("a","B")])
        XCTAssertFalse(ambiguous.node.isComplete)
        XCTAssertEqual(compareNodes(ambiguous.node,ambiguous.node),.unknown)
        XCTAssertEqual(diff(ambiguous,ambiguous),.unknown)
        XCTAssertEqual(try structuralDiff(ambiguous,ambiguous).kind,.unknown)
    }
    func testStringMapKeyAliasesUseOneExactPathIdentity() throws {
        let legacy = PathSegment.mapKey(ExactString("clé")),typed = PathSegment.typedMapKey(.string(ExactString("clé")))
        XCTAssertEqual(legacy,typed); XCTAssertEqual(Set([legacy,typed]).count,1)
        let first = InspectChild(segment:legacy,label:ExactString("a"),node:InspectNode(kind:.scalar(.bool(true))))
        let second = InspectChild(segment:typed,label:ExactString("b"),node:InspectNode(kind:.scalar(.bool(false))))
        let snapshot = SnapshotID(session:1,revision:0,sequence:0),schema = DisplaySchema(name:"map")
        let single = InspectResult(snapshot:snapshot,schema:schema,node:InspectNode(kind:.map(.stable),children:[first],childCount:1))
        XCTAssertEqual(try resolve(single,path:[typed],schemaVersion:1).node.kind,.scalar(.bool(true)))
        let ambiguous = InspectResult(snapshot:snapshot,schema:schema,node:InspectNode(kind:.map(.stable),children:[first,second],childCount:2))
        XCTAssertFalse(ambiguous.node.isComplete); XCTAssertThrowsError(try resolve(ambiguous,path:[typed],schemaVersion:1))
        XCTAssertNotEqual(legacy,PathSegment.mapKey(ExactString("cle\u{301}")))
    }
    func testResolveRejectsDuplicatePathAndChargesSiblingScan() throws {
        let first = InspectChild(segment:.field(ExactString("a")),label:ExactString("first"),node:InspectNode(kind:.scalar(.bool(true))))
        let other = InspectChild(segment:.field(ExactString("b")),label:ExactString("other"),node:InspectNode(kind:.scalar(.bool(false))))
        let duplicate = InspectChild(segment:.field(ExactString("a")),label:ExactString("second"),node:InspectNode(kind:.scalar(.bool(false))))
        let schema = DisplaySchema(name:"record"),snapshot = SnapshotID(session:1,revision:1,sequence:1)
        func result(_ children: [InspectChild]) -> InspectResult { InspectResult(snapshot:snapshot,schema:schema,node:InspectNode(kind:.object(ExactString("record")),children:children,childCount:children.count)) }
        let path: [PathSegment] = [.field(ExactString("a"))]
        XCTAssertThrowsError(try resolve(result([first,other,duplicate]),path:path,schemaVersion:1,maxWork:3)) { error in guard case InspectError.invalidMetadata = error else { return XCTFail("expected ambiguous path") } }
        XCTAssertThrowsError(try resolve(result([first,other,duplicate]),path:path,schemaVersion:1,maxWork:2)) { error in guard case InspectError.budgetExceeded = error else { return XCTFail("expected work limit") } }
        XCTAssertEqual(try resolve(result([first,other]),path:path,schemaVersion:1,maxWork:2).node.kind,.scalar(.bool(true)))
    }
    func testVariantPathMatchesRustStructureAndUTF8Identity() throws {
        let node = try InspectContext().enumeration(name: "Result",variantID: "ok",variantLabel: "Success") { context in
            [try context.field("é") { try $0.scalar(.bool(true)) }]
        }
        XCTAssertEqual(node.childCount,1)
        XCTAssertEqual(node.children.first?.segment,.variant(ExactString("ok")))
        let result = InspectResult(snapshot: SnapshotID(session: 1,revision: 0,sequence: 0),schema: DisplaySchema(name: "Result"),node: node)
        let path: [PathSegment] = [.variant(ExactString("ok")),.field(ExactString("é"))]
        XCTAssertEqual(try resolve(result,path: path,schemaVersion: 1).node.kind,.scalar(.bool(true)))
        XCTAssertThrowsError(try resolve(result,path: [.variant(ExactString("ok")),.field(ExactString("e\u{301}"))],schemaVersion: 1))
        XCTAssertNotEqual(DisplaySchema(name: "é"),DisplaySchema(name: "e\u{301}"))
    }
    func testSequenceAndMapMetadataRespectValueAndByteBudgets() throws {
        var limits = InspectLimits(); limits.maxValueBytes = 2
        let sequence = try InspectContext(limits: limits).sequence(name: "long",total: 0) { _,context in try context.scalar(.unit) }
        XCTAssertEqual(sequence.kind,.sequence(ExactString("long")))
        var calls = 0
        XCTAssertThrowsError(try InspectContext(limits: limits).map(ordering: .stable,total: 1) {
            InspectMapEntry(key: .string(ExactString("long")),label: "") { context in calls += 1; return try context.scalar(.unit) }
        })
        XCTAssertEqual(calls,0)
        limits.maxValueBytes = 32; limits.maxBytes = 2
        XCTAssertThrowsError(try InspectContext(limits: limits).map(ordering: .stable,total: 1) {
            InspectMapEntry(key: .string(ExactString("key")),label: "") { context in calls += 1; return try context.scalar(.unit) }
        })
        XCTAssertEqual(calls,0)
    }

}
