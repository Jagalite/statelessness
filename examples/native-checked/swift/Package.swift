// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "CheckedConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness", path: "../swift-package")],
 targets: [.executableTarget(name: "CheckedConsumer", dependencies: [.product(name: "Statelessness", package: "Statelessness")])])
