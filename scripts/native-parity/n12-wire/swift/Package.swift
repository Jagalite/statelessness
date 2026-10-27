// swift-tools-version: 6.0
import Foundation
import PackageDescription

// A copied consumer can resolve only the fresh copied library package. There
// are no source-file imports, private targets, or Rust-backed bindings here.
let libraryPath = ProcessInfo.processInfo.environment["N12_SWIFT_PACKAGE_PATH"]
    ?? URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("../../../..").standardizedFileURL.path
let package = Package(
    name: "N12WireSwift",
    platforms: [.macOS(.v11)],
    dependencies: [.package(name: "Statelessness", path: libraryPath)],
    targets: [
        .executableTarget(
            name: "n12-wire-swift",
            dependencies: [
                .product(name: "Statelessness", package: "Statelessness"),
                .product(name: "StatelessDebug", package: "Statelessness")
            ],
            path: "Sources"
        )
    ]
)
