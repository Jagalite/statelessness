// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "BFSConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness", path: "../swift-package")],
 targets: [.executableTarget(name: "BFSConsumer", dependencies: [.product(name: "Statelessness", package: "Statelessness")])])
