import Foundation
import StatelessConformance

do { try run() }
catch { try? FileHandle.standardError.write(contentsOf: Data("\(error)\n".utf8)); exit(1) }
