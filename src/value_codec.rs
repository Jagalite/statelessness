//! Canonical owned values: little-endian fixed integers, strict bools, u32 lengths.
//! Limits bound this reader's work and requested allocations, not callback memory.
use crate::{EncodeBuffer, ModelError};

#[derive(Clone, Copy, Debug)]
pub struct DecodeLimits {
    pub bytes: usize,
    pub allocation: usize,
    pub elements: usize,
    pub depth: usize,
    pub work: usize,
}
impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            bytes: 16 * 1024 * 1024,
            allocation: 32 * 1024 * 1024,
            elements: 1_000_000,
            depth: 64,
            work: 2_000_000,
        }
    }
}
pub trait TraceEncode {
    /// Diagnostic structural spelling, not a schema compatibility proof.
    fn trace_schema() -> &'static str
    where
        Self: Sized,
    {
        std::any::type_name::<Self>()
    }
    fn trace_variant(&self) -> Option<&'static str> {
        None
    }

    fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError>;
    fn trace_bytes(&self, maximum: usize) -> Result<Vec<u8>, ModelError> {
        let mut bytes = Vec::new();
        let mut out = EncodeBuffer::new(&mut bytes, maximum);
        self.trace_encode(&mut out)?;
        out.finish()?;
        Ok(bytes)
    }
}
pub trait TraceDecode: Sized {
    fn trace_decode(reader: &mut Decoder<'_>) -> Result<Self, ModelError>;
    fn from_trace(bytes: &[u8], limits: DecodeLimits) -> Result<Self, ModelError> {
        let mut reader = Decoder::new(bytes, limits)?;
        let value = reader.value()?;
        reader.finish()?;
        Ok(value)
    }
}
pub struct Decoder<'a> {
    bytes: &'a [u8],
    limits: DecodeLimits,
    depth: usize,
    failure: Option<ModelError>,
}
impl<'a> Decoder<'a> {
    pub fn new(bytes: &'a [u8], limits: DecodeLimits) -> Result<Self, ModelError> {
        if bytes.len() > limits.bytes {
            return Err(ModelError::new("decode byte limit"));
        }
        Ok(Self {
            bytes,
            limits,
            depth: 0,
            failure: None,
        })
    }
    fn reject<T>(&mut self, error: ModelError) -> Result<T, ModelError> {
        let first = self.failure.get_or_insert(error);
        Err(first.clone())
    }
    pub fn take(&mut self, n: usize) -> Result<&'a [u8], ModelError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        if n > self.bytes.len() {
            return self.reject(ModelError::new("truncated value"));
        }
        let (head, tail) = self.bytes.split_at(n);
        self.bytes = tail;
        Ok(head)
    }
    pub fn value<T: TraceDecode>(&mut self) -> Result<T, ModelError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        let Some(work) = self.limits.work.checked_sub(1) else {
            return self.reject(ModelError::new("decode work limit"));
        };
        self.limits.work = work;
        if self.depth >= self.limits.depth {
            return self.reject(ModelError::new("decode nesting limit"));
        }
        self.depth += 1;
        let result = T::trace_decode(self);
        self.depth -= 1;
        match result {
            Err(error) => self.reject(error),
            Ok(value) => match &self.failure {
                Some(error) => Err(error.clone()),
                None => Ok(value),
            },
        }
    }
    pub fn collection(&mut self, n: usize, element_size: usize) -> Result<(), ModelError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        let result = (|| {
            self.limits.elements = self
                .limits
                .elements
                .checked_sub(n)
                .ok_or_else(|| ModelError::new("decode element limit"))?;
            let allocation = n
                .checked_mul(element_size)
                .ok_or_else(|| ModelError::new("decode allocation overflow"))?;
            self.limits.allocation = self
                .limits
                .allocation
                .checked_sub(allocation)
                .ok_or_else(|| ModelError::new("decode allocation limit"))?;
            if n > self.limits.work {
                return Err(ModelError::new("decode work limit"));
            }
            Ok(())
        })();
        match result {
            Ok(()) => Ok(()),
            Err(error) => self.reject(error),
        }
    }
    pub fn finish(&self) -> Result<(), ModelError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        if self.bytes.is_empty() {
            Ok(())
        } else {
            Err(ModelError::new("trailing value bytes"))
        }
    }
}
macro_rules! integer { ($($t:ty),*) => {$ (
    impl TraceEncode for $t { fn trace_encode(&self,o:&mut EncodeBuffer<'_>)->Result<(),ModelError>{o.extend_from_slice(&self.to_le_bytes())} }
    impl TraceDecode for $t { fn trace_decode(r:&mut Decoder<'_>)->Result<Self,ModelError>{Ok(Self::from_le_bytes(r.take(std::mem::size_of::<Self>())?.try_into().map_err(|_|ModelError::new("integer width"))?))} }
)*}; }
integer!(u8, u16, u32, u64, u128, i8, i16, i32, i64, i128);
impl TraceEncode for bool {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        (*self as u8).trace_encode(o)
    }
}
impl TraceDecode for bool {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        match r.value::<u8>()? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(ModelError::new("invalid boolean")),
        }
    }
}
impl TraceEncode for () {
    fn trace_encode(&self, _: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        Ok(())
    }
}
impl TraceDecode for () {
    fn trace_decode(_: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(())
    }
}

/// Explicit length override, resolved through traits rather than type spellings.
pub trait LengthEncode {
    fn encode_length(&self, width: u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError>;
}
pub trait LengthDecode: Sized {
    fn decode_length(width: u8, reader: &mut Decoder<'_>) -> Result<Self, ModelError>;
}
fn write_len(n: usize, width: u8, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
    match width {
        1 => u8::try_from(n)
            .map_err(|_| ModelError::new("length overflow"))?
            .trace_encode(o),
        2 => u16::try_from(n)
            .map_err(|_| ModelError::new("length overflow"))?
            .trace_encode(o),
        4 => u32::try_from(n)
            .map_err(|_| ModelError::new("length overflow"))?
            .trace_encode(o),
        _ => Err(ModelError::new("unsupported length width")),
    }
}
fn read_len(width: u8, r: &mut Decoder<'_>) -> Result<usize, ModelError> {
    match width {
        1 => Ok(r.value::<u8>()? as usize),
        2 => Ok(r.value::<u16>()? as usize),
        4 => usize::try_from(r.value::<u32>()?).map_err(|_| ModelError::new("length overflow")),
        _ => Err(ModelError::new("unsupported length width")),
    }
}
impl<T: TraceEncode> LengthEncode for Vec<T> {
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        write_len(self.len(), w, o)?;
        for v in self {
            v.trace_encode(o)?;
        }
        Ok(())
    }
}
impl<T: TraceDecode> LengthDecode for Vec<T> {
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        let n = read_len(w, r)?;
        r.collection(n, std::mem::size_of::<T>())?;
        let mut values = Vec::new();
        values
            .try_reserve_exact(n)
            .map_err(|_| ModelError::new("decode allocation failed"))?;
        for _ in 0..n {
            values.push(r.value()?);
        }
        Ok(values)
    }
}
impl<T: TraceEncode> TraceEncode for Vec<T> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.encode_length(4, o)
    }
}
impl<T: TraceDecode> TraceDecode for Vec<T> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Self::decode_length(4, r)
    }
}
impl LengthEncode for String {
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        write_len(self.len(), w, o)?;
        o.extend_from_slice(self.as_bytes())
    }
}
impl LengthDecode for String {
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        let n = read_len(w, r)?;
        r.collection(n, 1)?;
        r.limits.work -= n;
        let bytes = r.take(n)?;
        let text = std::str::from_utf8(bytes).map_err(|_| ModelError::new("invalid UTF-8"))?;
        let mut value = String::new();
        value
            .try_reserve_exact(n)
            .map_err(|_| ModelError::new("decode allocation failed"))?;
        value.push_str(text);
        Ok(value)
    }
}
impl TraceEncode for String {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.encode_length(4, o)
    }
}
impl TraceDecode for String {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Self::decode_length(4, r)
    }
}
impl<T: TraceEncode> TraceEncode for Option<T> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        match self {
            None => 0u8.trace_encode(o),
            Some(v) => {
                1u8.trace_encode(o)?;
                v.trace_encode(o)
            }
        }
    }
}
impl<T: TraceDecode> TraceDecode for Option<T> {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        match r.value::<u8>()? {
            0 => Ok(None),
            1 => Ok(Some(r.value()?)),
            _ => Err(ModelError::new("invalid option tag")),
        }
    }
}
impl<T: TraceEncode, const N: usize> TraceEncode for [T; N] {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        for v in self {
            v.trace_encode(o)?;
        }
        Ok(())
    }
}
impl<T: TraceDecode, const N: usize> TraceDecode for [T; N] {
    fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
        r.collection(N, std::mem::size_of::<T>())?;
        let mut v = Vec::new();
        v.try_reserve_exact(N)
            .map_err(|_| ModelError::new("decode allocation failed"))?;
        for _ in 0..N {
            v.push(r.value()?);
        }
        v.try_into().map_err(|_| ModelError::new("array length"))
    }
}
macro_rules! tuple { ($($t:ident:$n:tt),+) => {
    impl<$($t:TraceEncode),+> TraceEncode for ($($t,)+){fn trace_encode(&self,o:&mut EncodeBuffer<'_>)->Result<(),ModelError>{$ (self.$n.trace_encode(o)?;)+ Ok(())}}
    impl<$($t:TraceDecode),+> TraceDecode for ($($t,)+){fn trace_decode(r:&mut Decoder<'_>)->Result<Self,ModelError>{Ok(($ (r.value::<$t>()?,)+))}}
};}
tuple!(A:0);
tuple!(A:0,B:1);
tuple!(A:0,B:1,C:2);
tuple!(A:0,B:1,C:2,D:3);

impl<S: TraceEncode, H: TraceEncode> TraceEncode for crate::OracleState<S, H> {
    fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.model.trace_encode(out)?;
        self.oracle.trace_encode(out)
    }
}
impl<S: TraceDecode, H: TraceDecode> TraceDecode for crate::OracleState<S, H> {
    fn trace_decode(reader: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self {
            model: reader.value()?,
            oracle: reader.value()?,
        })
    }
}
