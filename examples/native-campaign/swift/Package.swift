// swift-tools-version: 6.0
import Foundation
import PackageDescription

let nativePackage = ProcessInfo.processInfo.environment["STATELESS_SWIFT_PACKAGE_PATH"] ?? "../../.."

let package = Package(
    name: "CampaignConsumer",
    platforms: [.macOS(.v11)],
    products: [.executable(name: "CampaignConsumer", targets: ["CampaignConsumer"])],
    dependencies: [.package(name: "Statelessness", path: nativePackage)],
    targets: [.executableTarget(name: "CampaignConsumer", dependencies: [
        .product(name: "Statelessness", package: "Statelessness")
    ])]
)
