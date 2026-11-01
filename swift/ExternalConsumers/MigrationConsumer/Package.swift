// swift-tools-version: 6.0
import Foundation
import PackageDescription

// External consumer for the Swift-only migration extensions. It depends only on
// the public `Statelessness` product. Qualification points it at an exported copy.
let nativePackage = ProcessInfo.processInfo.environment["STATELESS_SWIFT_PACKAGE_PATH"] ?? "../../.."

let package = Package(
    name: "MigrationConsumer",
    platforms: [.macOS(.v11)],
    products: [.executable(name: "MigrationConsumer", targets: ["MigrationConsumer"])],
    dependencies: [.package(name: "Statelessness", path: nativePackage)],
    targets: [.executableTarget(name: "MigrationConsumer", dependencies: [
        .product(name: "Statelessness", package: "Statelessness")
    ])]
)
