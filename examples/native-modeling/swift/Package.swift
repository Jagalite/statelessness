// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "ModelingConsumer", platforms: [.macOS(.v11)], dependencies: [.package(path: "../swift-package")], targets: [.executableTarget(name: "ModelingConsumer", platforms: [.macOS(.v11)], dependencies: [.product(name: "Statelessness", package: "swift-package")])])
