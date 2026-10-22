// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "DebugConsumer", platforms: [.macOS(.v11)],
 dependencies: [.package(name: "Statelessness",path: "../swift-package")],
 targets: [.executableTarget(name: "DebugConsumer",dependencies: [.product(name: "StatelessDebug",package: "Statelessness"),.product(name: "Statelessness",package: "Statelessness")])])
