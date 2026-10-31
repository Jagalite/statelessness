import XCTest
@testable import Statelessness

private final class ContainsBox {
    var value: Int
    init(_ value: Int) { self.value = value }
}

final class DomainContainsSnapshotTests: XCTestCase {
    func testQueryIsCapturedBeforeComparisonMutatesAlias() throws {
        let descriptor = try DomainDescriptor(name: "inputs", assumptions: "finite", maxEntries: 2)
        var domain = Domain<ContainsBox>(descriptor, clone: { ContainsBox($0.value) })
        try domain.push(ContainsBox(1))
        try domain.push(ContainsBox(2))
        let query = ContainsBox(2)
        var comparisons = 0
        XCTAssertTrue(try domain.contains(query) { entry, candidate in
            comparisons += 1
            query.value = 9
            return entry.value == candidate.value
        })
        XCTAssertEqual(comparisons, 2)
    }

    func testEmptyDomainDoesNotCloneQuery() throws {
        let descriptor = try DomainDescriptor(name: "empty", assumptions: "finite", maxEntries: 0)
        let domain = Domain<ContainsBox>(descriptor, clone: { _ in throw ModelError("unexpected clone") })
        XCTAssertFalse(try domain.contains(ContainsBox(1)) { _, _ in XCTFail("unexpected comparison"); return false })
    }

    func testEntriesAreCapturedBeforeComparisonAndLaterFailureIsDeferred() throws {
        let descriptor = try DomainDescriptor(name: "inputs", assumptions: "finite", maxEntries: 2)
        var retained: [ContainsBox] = []
        var failSecond = false
        var domain = Domain<ContainsBox>(descriptor, clone: { box in
            if failSecond && box.value == 2 { throw ModelError("later copy") }
            let copy = ContainsBox(box.value)
            retained.append(copy)
            return copy
        })
        try domain.push(ContainsBox(1))
        try domain.push(ContainsBox(2))
        let secondStored = retained[1]
        XCTAssertTrue(try domain.contains(ContainsBox(2)) { entry, candidate in
            secondStored.value = 99
            return entry.value == candidate.value
        })
        secondStored.value = 2
        failSecond = true
        XCTAssertTrue(try domain.contains(ContainsBox(1)) { $0.value == $1.value })
        XCTAssertThrowsError(try domain.contains(ContainsBox(3)) { $0.value == $1.value })
    }
}
