// swift-tools-version: 6.0
import PackageDescription
let package = Package(name:"ReferenceConsumer",platforms:[.macOS(.v11)],dependencies:[.package(name:"Statelessness",path:"../../..")],targets:[.executableTarget(name:"ReferenceWitness",dependencies:[.product(name:"Statelessness",package:"Statelessness")])])
