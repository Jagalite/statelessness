/// Finite measured logical Rust64 descriptors, not a generic Rust layout algorithm.
/// Reference: Rust 1.99.0, aarch64-apple-darwin, usize64;
/// Kyoto f0351a737e3f43758f30430d8a92d4c4. OS/native heap layout is unrelated.
public enum Rust64ValueLayouts {
    /// Explicit selection by exact structural spelling. Unknown shapes return nil.
    /// The spelling is a lookup label, not an authenticated schema identity.
    public static func measured(_ spelling: String) -> Rust64ValueLayout? {
        switch spelling {
        case "(u16,)": return Rust64ValueLayout(size: 2, identity: "(u16,)", alignment: 2)
        case "(u16,i32,bool,Option<u8>)": return Rust64ValueLayout(size: 12, identity: "(u16,i32,bool,Option<u8>)", alignment: 4)
        case "(u8,u64)": return Rust64ValueLayout(size: 16, identity: "(u8,u64)", alignment: 8)
        case "(unit,i8,bool)": return Rust64ValueLayout(size: 2, identity: "(unit,i8,bool)", alignment: 1)
        case "Option<Option<unit>>": return Rust64ValueLayout(size: 1, identity: "Option<Option<unit>>", alignment: 1)
        case "Option<String>": return Rust64ValueLayout(size: 24, identity: "Option<String>", alignment: 8)
        case "Option<bool>": return Rust64ValueLayout(size: 1, identity: "Option<bool>", alignment: 1)
        case "Option<u64>": return Rust64ValueLayout(size: 16, identity: "Option<u64>", alignment: 8)
        case "Option<u8>": return Rust64ValueLayout(size: 2, identity: "Option<u8>", alignment: 1)
        case "Option<unit>": return Rust64ValueLayout(size: 1, identity: "Option<unit>", alignment: 1)
        case "OracleState<u16,String>": return Rust64ValueLayout(size: 32, identity: "OracleState<u16,String>", alignment: 8)
        case "String": return Rust64ValueLayout(size: 24, identity: "String", alignment: 8)
        case "Vec<Option<u64>>": return Rust64ValueLayout(size: 24, identity: "Vec<Option<u64>>", alignment: 8)
        case "Vec<T> header": return Rust64ValueLayout(size: 24, identity: "Vec<T> header", alignment: 8)
        case "Vec<Vec<u8>>": return Rust64ValueLayout(size: 24, identity: "Vec<Vec<u8>>", alignment: 8)
        case "Vec<[u16;3]>": return Rust64ValueLayout(size: 24, identity: "Vec<[u16;3]>", alignment: 8)
        case "Vec<u16>": return Rust64ValueLayout(size: 24, identity: "Vec<u16>", alignment: 8)
        case "Vec<u8>": return Rust64ValueLayout(size: 24, identity: "Vec<u8>", alignment: 8)
        case "Vec<unit>": return Rust64ValueLayout(size: 24, identity: "Vec<unit>", alignment: 8)
        case "[u16;1]": return Rust64ValueLayout(size: 2, identity: "[u16;1]", alignment: 2)
        case "[u16;3]": return Rust64ValueLayout(size: 6, identity: "[u16;3]", alignment: 2)
        case "[u8;0]": return Rust64ValueLayout(size: 0, identity: "[u8;0]", alignment: 1)
        case "bool": return Rust64ValueLayout(size: 1, identity: "bool", alignment: 1)
        case "i128": return Rust64ValueLayout(size: 16, identity: "i128", alignment: 16)
        case "i16": return Rust64ValueLayout(size: 2, identity: "i16", alignment: 2)
        case "i32": return Rust64ValueLayout(size: 4, identity: "i32", alignment: 4)
        case "i64": return Rust64ValueLayout(size: 8, identity: "i64", alignment: 8)
        case "i8": return Rust64ValueLayout(size: 1, identity: "i8", alignment: 1)
        case "u128": return Rust64ValueLayout(size: 16, identity: "u128", alignment: 16)
        case "u16": return Rust64ValueLayout(size: 2, identity: "u16", alignment: 2)
        case "u32": return Rust64ValueLayout(size: 4, identity: "u32", alignment: 4)
        case "u64": return Rust64ValueLayout(size: 8, identity: "u64", alignment: 8)
        case "u8": return Rust64ValueLayout(size: 1, identity: "u8", alignment: 1)
        case "unit": return Rust64ValueLayout(size: 0, identity: "unit", alignment: 1)
        default: return nil
        }
    }
    public static var optionU64: Rust64ValueLayout { measured("Option<u64>")! }
    public static var optionUnit: Rust64ValueLayout { measured("Option<unit>")! }
}
