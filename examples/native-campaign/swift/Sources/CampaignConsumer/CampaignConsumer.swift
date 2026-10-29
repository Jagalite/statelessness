import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

@main
struct CampaignConsumer {
    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            if args.count == 2 && args[0] == "--case" {
                try emit(runCase(args[1]))
            } else if args == ["--host-checks"] {
                try emit(hostChecks())
            } else if args == ["--native-policy-checks"] {
                try emit(nativePolicyChecks())
            } else if args == ["--list"] {
                try emit(["schema": "native-campaign-cases-v1", "cases": allCaseNames()])
            } else if args.isEmpty {
                // Construct all rows before emitting, so a failed adapter never
                // presents a successful-looking prefix as a complete campaign.
                let rows = try allCaseNames().map(runCase)
                for row in rows { try emit(row) }
            } else {
                throw AdapterError("usage: CampaignConsumer [--case ID | --host-checks | --native-policy-checks | --list]")
            }
        } catch {
            FileHandle.standardError.write(Data("native campaign consumer: \(error)\n".utf8))
            exit(1)
        }
    }
}
