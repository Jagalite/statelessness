// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "GuidedConsumer", platforms: [.macOS(.v11)],
    dependencies: [.package(name: "Statelessness", path: "../../..")],
    targets: [.executableTarget(name: "GuidedExample", dependencies: [.product(name: "Statelessness", package: "Statelessness")])]
)
