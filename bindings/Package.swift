// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "StatelessBindings",
    products: [
        .library(name: "StatelessNative", targets: ["StatelessNative"]),
        .executable(name: "swift-counter", targets: ["Counter"]),
    ],
    targets: [
        .systemLibrary(name: "Stateless", path: "c"),
        .target(name: "StatelessNative", dependencies: ["Stateless"],
                path: "swift/Sources", linkerSettings: [.linkedLibrary("stateless")]),
        .executableTarget(name: "Counter", dependencies: ["StatelessNative"],
                          path: "swift", exclude: ["Sources", "Tests", "README.md"], sources: ["Counter.swift"]),
        .testTarget(name: "NativeTests", dependencies: ["StatelessNative"], path: "swift/Tests"),
    ]
)
