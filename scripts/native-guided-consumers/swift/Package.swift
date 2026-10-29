// swift-tools-version: 6.0
import Foundation
import PackageDescription

// Require an explicit installed/copied native ROOT package. There is no checkout
// fallback and no dependency on the Rust-backed bindings package.
guard let nativePackage = ProcessInfo.processInfo.environment["STATELESS_SWIFT_PACKAGE"],
      !nativePackage.isEmpty else {
    fatalError("STATELESS_SWIFT_PACKAGE must name the copied native root package")
}

let package = Package(
    name: "GuidedConsumer",
    platforms: [.macOS(.v11)],
    products: [.executable(name: "GuidedConsumer", targets: ["GuidedConsumer"])],
    dependencies: [.package(name: "Statelessness", path: nativePackage)],
    targets: [.executableTarget(name: "GuidedConsumer", dependencies: [
        .product(name: "Statelessness", package: "Statelessness")
    ])]
)
