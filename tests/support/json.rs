//! Minimal test-only JSON reader for the shared conformance fixtures. The core
//! crate stays dependency-free, so tests parse the fixtures themselves.
#![allow(dead_code)]

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<Value>),
    Obj(Vec<(String, Value)>),
}

impl Value {
    pub fn get(&self, key: &str) -> &Value {
        match self {
            Value::Obj(fields) => fields
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v)
                .unwrap_or(&Value::Null),
            _ => &Value::Null,
        }
    }
    pub fn str(&self) -> &str {
        match self {
            Value::Str(s) => s,
            other => panic!("expected string, got {other:?}"),
        }
    }
    pub fn opt_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn int(&self) -> i64 {
        match self {
            Value::Int(n) => *n,
            other => panic!("expected integer, got {other:?}"),
        }
    }
    pub fn opt_int(&self) -> Option<i64> {
        match self {
            Value::Int(n) => Some(*n),
            _ => None,
        }
    }
    pub fn bool(&self) -> bool {
        match self {
            Value::Bool(b) => *b,
            other => panic!("expected bool, got {other:?}"),
        }
    }
    pub fn arr(&self) -> &[Value] {
        match self {
            Value::Arr(items) => items,
            other => panic!("expected array, got {other:?}"),
        }
    }
    pub fn obj(&self) -> &[(String, Value)] {
        match self {
            Value::Obj(fields) => fields,
            other => panic!("expected object, got {other:?}"),
        }
    }
    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }
    pub fn strings(&self) -> Vec<String> {
        self.arr().iter().map(|v| v.str().to_owned()).collect()
    }
    pub fn ints(&self) -> Vec<i64> {
        self.arr().iter().map(Value::int).collect()
    }
}

pub fn parse(text: &str) -> Value {
    let mut p = Parser {
        b: text.as_bytes(),
        i: 0,
    };
    let v = p.value();
    p.ws();
    assert_eq!(p.i, p.b.len(), "trailing JSON");
    v
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn ws(&mut self) {
        while self.i < self.b.len() && self.b[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
    }
    fn eat(&mut self, c: u8) -> bool {
        self.ws();
        if self.b.get(self.i) == Some(&c) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    fn string(&mut self) -> String {
        assert!(self.eat(b'"'));
        let mut out = String::new();
        loop {
            let c = self.b[self.i];
            self.i += 1;
            match c {
                b'"' => return out,
                b'\\' => {
                    let e = self.b[self.i];
                    self.i += 1;
                    match e {
                        b'n' => out.push('\n'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let hex = std::str::from_utf8(&self.b[self.i..self.i + 4]).unwrap();
                            self.i += 4;
                            out.push(
                                char::from_u32(u32::from_str_radix(hex, 16).unwrap()).unwrap(),
                            );
                        }
                        other => out.push(char::from(other)),
                    }
                }
                _ => {
                    let start = self.i - 1;
                    let mut end = self.i;
                    while end < self.b.len() && self.b[end] != b'"' && self.b[end] != b'\\' {
                        end += 1;
                    }
                    out.push_str(std::str::from_utf8(&self.b[start..end]).unwrap());
                    self.i = end;
                }
            }
        }
    }
    fn value(&mut self) -> Value {
        self.ws();
        match self.b[self.i] {
            b'"' => Value::Str(self.string()),
            b'{' => {
                self.i += 1;
                let mut fields = Vec::new();
                if self.eat(b'}') {
                    return Value::Obj(fields);
                }
                loop {
                    self.ws();
                    let k = self.string();
                    assert!(self.eat(b':'));
                    fields.push((k, self.value()));
                    if self.eat(b'}') {
                        return Value::Obj(fields);
                    }
                    assert!(self.eat(b','));
                }
            }
            b'[' => {
                self.i += 1;
                let mut items = Vec::new();
                if self.eat(b']') {
                    return Value::Arr(items);
                }
                loop {
                    items.push(self.value());
                    if self.eat(b']') {
                        return Value::Arr(items);
                    }
                    assert!(self.eat(b','));
                }
            }
            b't' => {
                self.i += 4;
                Value::Bool(true)
            }
            b'f' => {
                self.i += 5;
                Value::Bool(false)
            }
            b'n' => {
                self.i += 4;
                Value::Null
            }
            _ => {
                let start = self.i;
                while self.i < self.b.len()
                    && (self.b[self.i] == b'-' || self.b[self.i].is_ascii_digit())
                {
                    self.i += 1;
                }
                Value::Int(
                    std::str::from_utf8(&self.b[start..self.i])
                        .unwrap()
                        .parse()
                        .unwrap(),
                )
            }
        }
    }
}
