// swift-tools-version: 6.0
import PackageDescription

// The qualification driver installs a frozen package beside this consumer.
let package = Package(
    name: "ObservationConsumer",
    platforms: [.macOS(.v11)],
    dependencies: [.package(path: "../swift-package")],
    targets: [.executableTarget(name: "ObservationConsumer", dependencies: [
        .product(name: "Statelessness", package: "swift-package")
    ])]
)
