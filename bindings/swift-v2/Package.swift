// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "StatelessNativeV2",
    platforms: [.macOS(.v11)],
    products: [.library(name: "StatelessNativeV2", targets: ["StatelessNativeV2"]),
               .executable(name: "stateless-v2-corpus", targets: ["NativeV2Corpus"]),
               .executable(name: "stateless-v2-bench", targets: ["NativeV2Bench"])],
    dependencies: [.package(name: "Statelessness", path: "../..")],
    targets: [
        .systemLibrary(name: "StatelessV2C", path: "c"),
        .target(name: "StatelessNativeV2", dependencies: ["StatelessV2C", .product(name: "Statelessness", package: "Statelessness")], path: "Sources", linkerSettings: [.linkedLibrary("stateless")]),
        .executableTarget(name: "NativeV2Corpus", dependencies: ["StatelessNativeV2", .product(name: "StatelessConformance", package: "Statelessness")], path: "Corpus"),
        .executableTarget(name: "NativeV2Bench", dependencies: ["StatelessNativeV2"], path: "Benchmarks"),
        .testTarget(name: "NativeV2Tests", dependencies: ["StatelessNativeV2"], path: "Tests"),
    ],
    swiftLanguageModes: [.v5]
)
