import XCTest
import Statelessness
@testable import StatelessDebug

final class InspectionTests: XCTestCase {
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
    func testTypedRuntimeAdmissionAndStructuralWorkBudget() throws {
        let context = try InspectContext()
        for (kind,text) in [("u8","256"),("i8","128"),("i128","170141183460469231731687303715884105728"),("u64","01"),("fake","1")] {
            XCTAssertThrowsError(try context.scalar(.integer(kind: ExactString(kind),decimal: ExactString(text))))
        }
        XCTAssertThrowsError(try context.scalar(.character(0xD800)))
        XCTAssertThrowsError(try context.scalar(.float(kind: ExactString("f32"),decimal: ExactString("1"),bits: UInt64.max)))
        XCTAssertNoThrow(try context.scalar(.integer(kind: ExactString("u128"),decimal: ExactString("340282366920938463463374607431768211455"))))
        let node = try InspectContext().object("x") { c in [try c.field("a") { try $0.scalar(.bool(true)) }] }
        let result = InspectResult(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),schema: DisplaySchema(name: "x"),node: node)
        XCTAssertTrue(try structuralDiff(result,result,maxWork: 0).truncated)
        XCTAssertEqual(try structuralDiff(result,result,maxWork: 1).kind,.unknown)
        XCTAssertEqual(try structuralDiff(result,result,maxWork: 100).kind,.unchanged)
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
        XCTAssertThrowsError(try InspectContext(limits: limits).sequence(name: "long",total: 0) { _,context in try context.scalar(.unit) })
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
