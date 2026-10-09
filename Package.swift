// swift-tools-version: 6.0
import PackageDescription

// Native Swift engine. The existing bindings/ package remains Rust-backed.
let package = Package(
    name: "Statelessness",
    platforms: [.macOS(.v11), .iOS(.v14), .tvOS(.v14), .watchOS(.v7)],
    products: [
        .library(name: "Statelessness", targets: ["Statelessness"]),
        .library(name: "StatelessConformance", targets: ["StatelessConformance"]),
        .executable(name: "stateless-corpus", targets: ["StatelessCorpus"])
    ],
    targets: [
        .target(name: "Statelessness", path: "swift/Sources/Statelessness"),
        .target(name: "StatelessConformance", dependencies: ["Statelessness"], path: "swift/Sources/StatelessConformance"),
        .executableTarget(name: "StatelessCorpus", dependencies: ["StatelessConformance"], path: "swift/Sources/StatelessCorpus"),
        .testTarget(name: "StatelessnessTests", dependencies: ["Statelessness", "StatelessConformance"], path: "swift/Tests/StatelessnessTests")
    ]
)
