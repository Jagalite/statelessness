// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "ModelConsumer", platforms: [.macOS(.v11)], dependencies: [.package(path: "../swift-package")], targets: [.executableTarget(name: "ModelConsumer", dependencies: [.product(name: "Statelessness", package: "swift-package")])])
