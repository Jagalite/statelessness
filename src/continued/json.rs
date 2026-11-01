//! Strict integer-only JSON for the continued trace format, matching the
//! Swift reference reader and writer: UTF-8 input, nesting depth 64, no
//! duplicate keys, no floating numbers, and rendering with object keys sorted
//! by UTF-8 bytes and no insignificant whitespace.

use std::collections::BTreeMap;

use crate::trace::TraceError;

const MAX_DEPTH: usize = 64;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Json {
    Null,
    Bool(bool),
    /// Integer spelling, never routed through floating point.
    Number(String),
    String(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}

fn invalid(message: &'static str) -> TraceError {
    TraceError::InvalidData(message)
}

fn valid_number(text: &str) -> bool {
    let bytes = text.as_bytes();
    let start = usize::from(bytes.first() == Some(&b'-'));
    if start >= bytes.len() || !bytes[start..].iter().all(u8::is_ascii_digit) {
        return false;
    }
    if bytes[start] == b'0' && bytes.len() > start + 1 {
        return false;
    }
    if start == 1 {
        text.parse::<i64>().is_ok()
    } else {
        text.parse::<u64>().is_ok()
    }
}

struct Parser<'a> {
    bytes: &'a [u8],
    index: usize,
}

impl Parser<'_> {
    fn space(&mut self) {
        while self
            .bytes
            .get(self.index)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\n' | b'\r'))
        {
            self.index += 1;
        }
    }
    fn take(&mut self, byte: u8) -> bool {
        if self.bytes.get(self.index) == Some(&byte) {
            self.index += 1;
            true
        } else {
            false
        }
    }
    fn expect(&mut self, byte: u8) -> Result<(), TraceError> {
        if self.take(byte) {
            Ok(())
        } else {
            Err(invalid("unexpected JSON byte"))
        }
    }
    fn hex4(&mut self) -> Result<u32, TraceError> {
        let digits = self
            .bytes
            .get(self.index..self.index + 4)
            .ok_or(invalid("truncated Unicode escape"))?;
        self.index += 4;
        digits.iter().try_fold(0u32, |value, &b| {
            let digit = match b {
                b'0'..=b'9' => b - b'0',
                b'A'..=b'F' => b - b'A' + 10,
                b'a'..=b'f' => b - b'a' + 10,
                _ => return Err(invalid("invalid Unicode escape")),
            };
            Ok(value * 16 + u32::from(digit))
        })
    }
    fn quoted(&mut self) -> Result<String, TraceError> {
        self.expect(b'"')?;
        let mut result = Vec::new();
        while let Some(&b) = self.bytes.get(self.index) {
            self.index += 1;
            match b {
                b'"' => {
                    return String::from_utf8(result).map_err(|_| invalid("invalid string UTF-8"));
                }
                0..=31 => return Err(invalid("unescaped control character")),
                b'\\' => {}
                _ => {
                    result.push(b);
                    continue;
                }
            }
            let escape = *self
                .bytes
                .get(self.index)
                .ok_or(invalid("truncated escape"))?;
            self.index += 1;
            match escape {
                b'"' | b'\\' | b'/' => result.push(escape),
                b'b' => result.push(8),
                b'f' => result.push(12),
                b'n' => result.push(b'\n'),
                b'r' => result.push(b'\r'),
                b't' => result.push(b'\t'),
                b'u' => {
                    let mut value = self.hex4()?;
                    if (0xdc00..=0xdfff).contains(&value) {
                        return Err(invalid("unpaired low surrogate"));
                    }
                    if (0xd800..=0xdbff).contains(&value) {
                        self.expect(b'\\')?;
                        self.expect(b'u')?;
                        let low = self.hex4()?;
                        if !(0xdc00..=0xdfff).contains(&low) {
                            return Err(invalid("unpaired high surrogate"));
                        }
                        value = 0x10000 + (value - 0xd800) * 1024 + low - 0xdc00;
                    }
                    let scalar = char::from_u32(value).ok_or(invalid("invalid Unicode scalar"))?;
                    let mut buffer = [0u8; 4];
                    result.extend_from_slice(scalar.encode_utf8(&mut buffer).as_bytes());
                }
                _ => return Err(invalid("invalid escape")),
            }
        }
        Err(invalid("unterminated string"))
    }
    fn value(&mut self, depth: usize) -> Result<Json, TraceError> {
        if depth > MAX_DEPTH {
            return Err(invalid("JSON nesting limit"));
        }
        self.space();
        let Some(&first) = self.bytes.get(self.index) else {
            return Err(invalid("missing JSON value"));
        };
        if first == b'"' {
            return Ok(Json::String(self.quoted()?));
        }
        if self.take(b'{') {
            let mut result = BTreeMap::new();
            self.space();
            if self.take(b'}') {
                return Ok(Json::Object(result));
            }
            loop {
                self.space();
                let key = self.quoted()?;
                self.space();
                self.expect(b':')?;
                if result.contains_key(&key) {
                    return Err(invalid("duplicate object key"));
                }
                let value = self.value(depth + 1)?;
                result.insert(key, value);
                self.space();
                if self.take(b'}') {
                    return Ok(Json::Object(result));
                }
                self.expect(b',')?;
            }
        }
        if self.take(b'[') {
            let mut result = Vec::new();
            self.space();
            if self.take(b']') {
                return Ok(Json::Array(result));
            }
            loop {
                result.push(self.value(depth + 1)?);
                self.space();
                if self.take(b']') {
                    return Ok(Json::Array(result));
                }
                self.expect(b',')?;
            }
        }
        for (word, value) in [
            ("null", Json::Null),
            ("true", Json::Bool(true)),
            ("false", Json::Bool(false)),
        ] {
            if self.bytes[self.index..].starts_with(word.as_bytes()) {
                self.index += word.len();
                return Ok(value);
            }
        }
        let start = self.index;
        self.take(b'-');
        while self.bytes.get(self.index).is_some_and(u8::is_ascii_digit) {
            self.index += 1;
        }
        // The scanned range is ASCII.
        let token = std::str::from_utf8(&self.bytes[start..self.index]).unwrap_or("");
        if !valid_number(token) {
            return Err(invalid("invalid integer"));
        }
        if self
            .bytes
            .get(self.index)
            .is_some_and(|b| matches!(b, b'.' | b'e' | b'E'))
        {
            return Err(invalid("floating numbers are outside the profile"));
        }
        Ok(Json::Number(token.to_owned()))
    }
}

pub(crate) fn parse(bytes: &[u8], maximum: u64) -> Result<Json, TraceError> {
    if bytes.len() as u64 > maximum {
        return Err(TraceError::LimitExceeded("JSON bytes"));
    }
    if std::str::from_utf8(bytes).is_err() {
        return Err(invalid("invalid JSON UTF-8"));
    }
    let mut parser = Parser { bytes, index: 0 };
    let value = parser.value(0)?;
    parser.space();
    if parser.index != bytes.len() {
        return Err(invalid("trailing JSON data"));
    }
    Ok(value)
}

struct Writer {
    output: Vec<u8>,
    maximum: u64,
}

impl Writer {
    fn write(&mut self, bytes: &[u8]) -> Result<(), TraceError> {
        if (self.output.len() + bytes.len()) as u64 > self.maximum {
            return Err(TraceError::LimitExceeded("JSON bytes"));
        }
        self.output.extend_from_slice(bytes);
        Ok(())
    }
    fn text(&mut self, value: &str) -> Result<(), TraceError> {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        self.write(b"\"")?;
        for &b in value.as_bytes() {
            match b {
                b'"' | b'\\' => self.write(&[b'\\', b])?,
                0..=31 => self.write(&[
                    b'\\',
                    b'u',
                    b'0',
                    b'0',
                    HEX[usize::from(b >> 4)],
                    HEX[usize::from(b & 15)],
                ])?,
                _ => self.write(&[b])?,
            }
        }
        self.write(b"\"")
    }
    fn value(&mut self, value: &Json, depth: usize) -> Result<(), TraceError> {
        if depth > MAX_DEPTH {
            return Err(invalid("JSON nesting limit"));
        }
        match value {
            Json::Null => self.write(b"null"),
            Json::Bool(b) => self.write(if *b { b"true" } else { b"false" }),
            Json::Number(n) => {
                if !valid_number(n) {
                    return Err(invalid("invalid integer"));
                }
                self.write(n.as_bytes())
            }
            Json::String(s) => self.text(s),
            Json::Array(items) => {
                self.write(b"[")?;
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        self.write(b",")?;
                    }
                    self.value(item, depth + 1)?;
                }
                self.write(b"]")
            }
            Json::Object(fields) => {
                // BTreeMap<String> iterates in UTF-8 byte order.
                self.write(b"{")?;
                for (index, (key, item)) in fields.iter().enumerate() {
                    if index > 0 {
                        self.write(b",")?;
                    }
                    self.text(key)?;
                    self.write(b":")?;
                    self.value(item, depth + 1)?;
                }
                self.write(b"}")
            }
        }
    }
}

pub(crate) fn render(value: &Json, maximum: u64) -> Result<Vec<u8>, TraceError> {
    let mut writer = Writer {
        output: Vec::new(),
        maximum,
    };
    writer.value(value, 0)?;
    Ok(writer.output)
}

pub(crate) fn object(
    value: Json,
    required: &[&str],
    optional: &[&str],
) -> Result<BTreeMap<String, Json>, TraceError> {
    let Json::Object(fields) = value else {
        return Err(invalid("expected object"));
    };
    if !required.iter().all(|k| fields.contains_key(*k))
        || !fields
            .keys()
            .all(|k| required.contains(&k.as_str()) || optional.contains(&k.as_str()))
    {
        return Err(invalid("missing or unknown object field"));
    }
    Ok(fields)
}

pub(crate) fn string(value: Option<Json>) -> Result<String, TraceError> {
    match value {
        Some(Json::String(s)) => Ok(s),
        _ => Err(invalid("expected string")),
    }
}

/// A nonnegative integer up to `maximum` (`-0` reads as zero).
pub(crate) fn integer(value: Option<&Json>, maximum: u64) -> Result<u64, TraceError> {
    match value {
        Some(Json::Number(n)) if valid_number(n) => {
            let n = if n == "-0" { "0" } else { n.as_str() };
            n.parse::<u64>()
                .ok()
                .filter(|&n| n <= maximum)
                .ok_or(invalid("invalid integer"))
        }
        _ => Err(invalid("invalid integer")),
    }
}

pub(crate) fn array(value: Option<Json>, maximum: usize) -> Result<Vec<Json>, TraceError> {
    match value {
        Some(Json::Array(items)) if items.len() <= maximum => Ok(items),
        _ => Err(invalid("expected bounded array")),
    }
}
