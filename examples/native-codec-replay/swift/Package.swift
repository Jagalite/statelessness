// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "CodecReplayConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness", path: "../swift-package")],
 targets: [.executableTarget(name: "CodecReplayConsumer", dependencies: [.product(name: "Statelessness", package: "Statelessness")])])
