"""Optional structural display projections, never replay or equality keys.

Adapters must be side-effect-free and cooperative. Budgets charge owned logical
projection payloads and traversal, not application allocations or process RSS.
No implicit reflection, repr(), or codec decoding is used.
Unbudgeted public node completeness checks cap individual strings at 256 Ki
code points. Admitted projections use their explicit inspection limits.
"""
from dataclasses import dataclass
from .core import ModelError, _natural, _copy


@dataclass(frozen=True)
class SnapshotId:
    session: int = 0
    revision: int = 0
    sequence: int = 0
    def __post_init__(self):
        for key in ('session','revision','sequence'): _natural(getattr(self,key),key)


@dataclass(frozen=True)
class DisplaySchema:
    name: str
    version: int = 1
    source: tuple[str,int,int] | None = None


@dataclass(frozen=True)
class PathSegment:
    kind: str
    value: object
    def __post_init__(self):
        if self.kind not in ('field','variant','index','map-key'): raise ValueError('invalid path segment')
        if self.kind == 'index': _natural(self.value,'index')
        elif self.kind == 'map-key': _key(self.value)
        elif type(self.value) is not str: raise ValueError('path identity must be str')


@dataclass(frozen=True)
class InspectLimits:
    max_work: int = 10_000
    max_depth: int = 32
    max_nodes: int = 1024
    max_bytes: int = 256*1024
    max_value_bytes: int = 4096
    max_page_size: int = 100
    def __post_init__(self):
        for key in self.__dataclass_fields__: _natural(getattr(self,key),key)


@dataclass(frozen=True)
class PageRequest:
    offset: int = 0
    limit: int = 100
    def __post_init__(self): _natural(self.offset,'offset'); _natural(self.limit,'limit',1)


@dataclass(frozen=True)
class PageInfo:
    offset: int
    returned: int
    total: int
    next_offset: int | None
    def __post_init__(self):
        for name in ('offset','returned','total'): _natural(getattr(self,name),name)
        if self.next_offset is not None: _natural(self.next_offset,'next_offset')


@dataclass(frozen=True)
class ChildNode:
    segment: PathSegment
    label: str
    node: object
    source: object = None


@dataclass(frozen=True)
class InspectNode:
    kind: str
    value: object = None
    children: tuple[ChildNode,...] = ()
    child_count: int | None = 0
    page: PageInfo | None = None
    completeness: str = 'complete'
    total_bytes: int | None = None
    ordering: str | None = None
    def __post_init__(self):
        if self.kind not in ('scalar','string','bytes','object','enum','sequence','map','opaque','redacted','unavailable','truncated'): raise ValueError('invalid projected kind')
        if type(self.children) is not tuple: raise ValueError('projected children must be tuple')
        if self.child_count is not None: _natural(self.child_count,'child count')
        if self.total_bytes is not None: _natural(self.total_bytes,'value byte count')
    @property
    def is_complete(self):
        return self.complete_with_budget(256*1024)
    def complete_with_budget(self, max_chars):
        """Check completeness allowing at most max_chars per string payload.

        Use the projection's configured value budget for larger admitted nodes.
        """
        _natural(max_chars,'completeness budget')
        if self.completeness != 'complete' or self.kind in ('opaque','redacted','unavailable','truncated'): return False
        if self.kind == 'map' and self.ordering != 'stable': return False
        if self.kind in ('object','enum','sequence','map') and self.child_count is None: return False
        if self.kind in ('string','bytes') and _payload_bytes(self.value,max_chars) != self.total_bytes: return False
        if self.child_count is not None and self.child_count != len(self.children): return False
        if self.page is not None and (self.page.offset != 0 or self.page.returned != len(self.children) or self.page.total != len(self.children) or self.page.next_offset is not None): return False
        segments=set()
        for child in self.children:
            if child.segment in segments or not child.node.complete_with_budget(max_chars): return False
            segments.add(child.segment)
        return True


@dataclass(frozen=True)
class InspectQuery:
    snapshot: SnapshotId = SnapshotId()
    path: tuple[PathSegment,...] = ()
    schema_version: int = 1
    page: PageRequest = PageRequest()
    limits: InspectLimits = InspectLimits()


@dataclass(frozen=True)
class InspectResult:
    snapshot: SnapshotId
    schema: DisplaySchema
    path: tuple[PathSegment,...]
    node: InspectNode


@dataclass(frozen=True)
class FieldView:
    id: str
    label: str
    value: object = None
    source: object = None
    hidden: bool = False
    @classmethod
    def redacted(cls,id,label): return cls(id,label,hidden=True)


@dataclass(frozen=True)
class ObjectView:
    name: str
    fields: tuple[FieldView,...]
    def schema(self): return DisplaySchema(self.name)
    def inspect(self,path,cx): return cx.object(path,self.name,self.fields)


@dataclass(frozen=True)
class EnumView:
    name: str
    variant_id: str
    variant_label: str
    fields: tuple[FieldView,...] = ()
    def schema(self): return DisplaySchema(self.name)
    def inspect(self,path,cx): return cx.enumeration(path,self.name,self.variant_id,self.variant_label,self.fields)


@dataclass(frozen=True)
class IntegerView:
    kind: str
    decimal: str
    def __post_init__(self): _integer(self.kind,self.decimal)
    def inspect(self,path,cx): return cx.scalar(path,(self.kind,self.decimal))


@dataclass(frozen=True)
class CharView:
    value: str
    def __post_init__(self):
        if type(self.value) is not str or len(self.value)!=1 or 0xD800<=ord(self.value)<=0xDFFF: raise ValueError('expected Unicode scalar')
    def inspect(self,path,cx): return cx.scalar(path,('char',self.value))


@dataclass(frozen=True)
class FloatView:
    """Local Rust-compatible float identity, with a bounded kind label.

    Existing f32/f64 values use ``(kind, decimal, bits)``; other kinds use
    ``('float', kind, decimal, bits)`` so a float named ``u8`` remains distinct
    from an integer of that type. The inbound wire parser has separate rules.
    """
    kind: str
    decimal: str
    bits: int
    def __post_init__(self):
        # Rust's local Scalar::Float carries a kind string and u64 bits; its
        # protocol parser imposes separate restrictions on inbound values.
        _label(self.kind); _label(self.decimal); _natural(self.bits,'float bits',maximum=(1<<64)-1)
    def inspect(self,path,cx):
        value=(self.kind,self.decimal,self.bits) if self.kind in ('f32','f64') else ('float',self.kind,self.decimal,self.bits)
        return cx.scalar(path,value)


def _float_parts(value):
    if type(value) is tuple and len(value)==3 and value[0] in ('f32','f64'):
        return value
    if type(value) is tuple and len(value)==4 and value[0]=='float' and value[1] not in ('f32','f64'):
        return value[1:]
    return None


def _scalar_wire_type(node):
    parts=_float_parts(node.value)
    return parts[0] if parts is not None else node.value[0]


class InspectError(ModelError): pass


def _label(value):
    if type(value) is not str: raise InspectError('metadata must be str')
    # Each valid Unicode scalar requires at least one UTF-8 byte. Reject an
    # oversized string before encoding it, keeping the temporary allocation and
    # work bounded even when an adapter returns arbitrarily large metadata.
    if len(value)>4096: raise InspectError('metadata value limit')
    try: size=len(value.encode('utf8'))
    except UnicodeEncodeError as error: raise InspectError('metadata must be UTF-8') from error
    if size>4096: raise InspectError('metadata value limit')
    return value


def _source(value):
    if value is None: return
    if type(value) is not tuple or len(value)!=3: raise InspectError('invalid source site')
    _label(value[0]); _natural(value[1],'source line',maximum=(1<<32)-1); _natural(value[2],'source column',maximum=(1<<32)-1)


def _integer(kind,value):
    _label(value)
    if kind!='python-int':
        if kind not in ('i8','i16','i32','i64','i128','isize','u8','u16','u32','u64','u128','usize'): raise InspectError('invalid integer type')
        # Rust Scalar::Integer retains the type and arbitrary decimal text.
        # The debugger wire parser has its own numeric grammar and bounds.
        return
    text=value[1:] if value.startswith('-') else value
    if not text or not text.isascii() or not text.isdigit() or (len(text)>1 and text[0]=='0') or value=='-0': raise InspectError('invalid integer scalar')


def _key(value):
    if type(value) is str: _label(value); return
    if type(value) is bool: return
    if type(value) is tuple and len(value)==2:
        if value[0]=='char': CharView(value[1]); return
        # Rust's local MapKey stores an integer type and an arbitrary string;
        # numeric grammar and range checks belong to the wire parser. Keep the
        # Python-specific integer identity, and bound metadata for both cases.
        if type(value[0]) is not str or value[0] not in ('python-int','i8','i16','i32','i64','i128','isize','u8','u16','u32','u64','u128','usize'):
            raise InspectError('invalid integer type')
        _label(value[1]); return
    raise InspectError('invalid typed map key')


def _node_shape(node, max_chars=256*1024):
    reasons=('pagination','sampled','observation-gap','schema-changed','work','depth','nodes','bytes','value-limit','redacted','opaque','unavailable')
    if type(node.completeness) is not str or node.completeness not in ('complete',*reasons): raise InspectError('invalid completeness')
    if node.kind=='truncated' and node.completeness not in reasons: raise InspectError('invalid truncation reason')
    if node.kind=='map' and node.ordering not in ('stable','unstable'): raise InspectError('invalid map ordering')
    if node.kind in ('string','bytes') and node.total_bytes is None: raise InspectError('invalid value byte count')
    if node.page is not None and type(node.page) is not PageInfo: raise InspectError('invalid page')
    if node.kind=='scalar':
        if type(node.value) is not tuple or len(node.value) not in (2,3,4): raise InspectError('invalid scalar')
        parts=_float_parts(node.value)
        if parts is not None:
            FloatView(*parts); return
        kind,value=node.value[:2]
        if len(node.value)!=2: raise InspectError('invalid scalar')
        if kind=='unit':
            if value is not None: raise InspectError('invalid unit scalar')
        elif kind=='bool':
            if type(value) is not bool: raise InspectError('invalid boolean scalar')
        elif kind in ('python-int','i8','i16','i32','i64','i128','isize','u8','u16','u32','u64','u128','usize'):
            _integer(kind,value)
        elif kind=='char': CharView(value)
        else: raise InspectError('invalid scalar kind')
    elif node.kind=='string':
        if type(node.value) is not str: raise InspectError('invalid string projection')
        try: _payload_bytes(node.value,max_chars)
        except UnicodeEncodeError as error: raise InspectError('string must be UTF-8') from error
    elif node.kind in ('object','sequence'): _label(node.value)
    elif node.kind=='opaque':
        if type(node.value) is not str: raise InspectError('invalid opaque projection')
        # Opaque labels are value previews, not 4096-byte metadata labels.
        # Bound and validate custom adapter output before admitting it.
        try: _payload_bytes(node.value,max_chars)
        except UnicodeEncodeError as error: raise InspectError('metadata must be UTF-8') from error
    elif node.kind=='bytes':
        if type(node.value) is not bytes: raise InspectError('invalid bytes projection')
    elif node.kind=='enum':
        if type(node.value) is not tuple or len(node.value)!=3: raise InspectError('invalid enum metadata')
        for value in node.value: _label(value)


def _payload_bytes(value, max_chars=256*1024):
    if type(value) is str:
        if len(value)>max_chars: raise InspectError('projection value limit')
        # Count exactly without materializing a second copy of the entire
        # payload. Retained-byte accounting must use the exact count, including
        # for multibyte characters that cross a chunk boundary.
        return sum(len(value[i:i+4096].encode('utf8')) for i in range(0,len(value),4096))
    if type(value) is bytes: return len(value)
    if type(value) in (tuple,list): return sum(_payload_bytes(v,max_chars) for v in value)
    if type(value) in (int,bool) or value is None: return 16
    return 0


class InspectContext:
    def __init__(self, limits=InspectLimits(), page=PageRequest()):
        self.limits,self.page,self.depth = limits,page,0
        self.work=self.nodes=self.bytes=0
    def charge_work(self, amount):
        _natural(amount,'work')
        if amount > self.limits.max_work-self.work: raise InspectError('work limit')
        self.work += amount
    def _node(self,node):
        self.charge_work(1)
        # Fixed logical node/child headers; not CPython allocator/RSS accounting.
        size=128+_payload_bytes(node.value,self.limits.max_bytes)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value) for c in node.children)
        if self.nodes >= self.limits.max_nodes or size > self.limits.max_bytes-self.bytes: raise InspectError('projection budget exceeded')
        self.nodes += 1; self.bytes += size
        return node
    def _leaf(self,path,node):
        if path: raise InspectError('path not found')
        return self._node(node)
    def scalar(self,path,value): return self._leaf(path,InspectNode('scalar',value))
    def string(self,path,value):
        maximum=min(self.limits.max_value_bytes,max(0,self.limits.max_bytes-self.bytes-128))
        preview=bytearray(); total=0; stopped=False
        for character in value:
            self.charge_work(1)
            try: encoded=character.encode('utf8')
            except UnicodeEncodeError as error: raise InspectError('string must be UTF-8') from error
            total+=len(encoded)
            if not stopped and len(encoded)<=maximum-len(preview): preview.extend(encoded)
            else: stopped=True
        return self._leaf(path,InspectNode('string',bytes(preview).decode('utf8'),total_bytes=total,completeness='complete' if len(preview)==total else 'value-limit'))
    def byte_string(self,path,value):
        maximum=min(self.limits.max_value_bytes,max(0,self.limits.max_bytes-self.bytes-128))
        return self._leaf(path,InspectNode('bytes',value[:maximum],total_bytes=len(value),completeness='complete' if len(value)<=maximum else 'value-limit'))
    def redacted(self,path=()): return self._leaf(path,InspectNode('redacted',completeness='redacted'))
    def unavailable(self,path=()): return self._leaf(path,InspectNode('unavailable',completeness='unavailable'))
    def opaque(self,path,label):
        if type(label) is not str: raise InspectError('metadata must be str')
        # Validate the whole label within the work budget while retaining only
        # the UTF-8 boundary-preserving preview, never a full encoded copy.
        preview=bytearray(); stopped=False
        for character in label:
            self.charge_work(1)
            try: encoded=character.encode('utf8')
            except UnicodeEncodeError as error: raise InspectError('metadata must be UTF-8') from error
            if not stopped and len(encoded)<=self.limits.max_value_bytes-len(preview): preview.extend(encoded)
            else: stopped=True
        return self._leaf(path,InspectNode('opaque',preview.decode('utf8'),completeness='opaque'))
    def _child(self,value):
        if self.depth >= self.limits.max_depth: return self._node(InspectNode('truncated',completeness='depth'))
        old_page,self.page=self.page,PageRequest(0,self.page.limit)
        self.depth += 1
        try: return _project(value,(),self)
        finally: self.depth -= 1; self.page=old_page
    def _container(self,kind,value,total,get,*,ordering=None):
        self._node(InspectNode(kind,value,child_count=total,ordering=ordering))
        offset=min(self.page.offset,total)
        end=min(total,offset+self.page.limit)
        children=[]; completeness='complete'
        if self.depth>=self.limits.max_depth and total:
            return InspectNode(kind,value,(),total,PageInfo(offset,0,total,offset),'depth',ordering=ordering)
        for index in range(offset,end):
            old_nodes,old_bytes=self.nodes,self.bytes
            try:
                self.charge_work(1)
                child=get(index)
                overhead=96+_payload_bytes(child.label)+_payload_bytes(child.segment.value)+_payload_bytes(child.source)
                if overhead>self.limits.max_bytes-self.bytes:
                    raise InspectError('projection budget exceeded')
                self.bytes+=overhead
            except InspectError as error:
                if str(error) not in ('work limit','projection budget exceeded'): raise
                self.nodes,self.bytes=old_nodes,old_bytes
                completeness=('work' if str(error)=='work limit' else
                              'nodes' if old_nodes>=self.limits.max_nodes else 'bytes')
                break
            if child.node.completeness!='complete' and completeness=='complete':
                completeness=child.node.completeness
            children.append(child)
        after=offset+len(children)
        if completeness=='complete' and (offset>0 or after<total): completeness='pagination'
        page=PageInfo(offset,len(children),total,after if after<total else None)
        return InspectNode(kind,value,tuple(children),total,page,completeness,ordering=ordering)
    def object(self,path,name,fields):
        name=_label(name)
        if path:
            if path[0].kind!='field': raise InspectError('wrong path segment')
            selected=None
            for field in fields:
                self.charge_work(1)
                if field.id==path[0].value:
                    if selected is not None: raise InspectError('invalid metadata')
                    selected=field
            if selected is None: raise InspectError('path not found')
            return self.redacted(path[1:]) if selected.hidden else _project(selected.value,path[1:],self)
        def get(index):
            field=fields[index]
            node=self.redacted() if field.hidden else self._child(field.value)
            return ChildNode(PathSegment('field',_label(field.id)),_label(field.label),node,field.source)
        return self._container('object',name,len(fields),get)
    def enumeration(self,path,name,variant_id,variant_label,fields):
        if path:
            if path[0]!=PathSegment('variant',variant_id): raise InspectError('path not found')
            return self.object(path[1:],variant_label,fields)
        def get(index):
            return ChildNode(PathSegment('variant',variant_id),variant_label,self._child(ObjectView(variant_label,fields)))
        return self._container('enum',(_label(name),_label(variant_id),_label(variant_label)),1,get)
    def sequence(self,path,values,name='sequence'):
        if path:
            if path[0].kind!='index': raise InspectError('wrong path segment')
            index=path[0].value
            if index>=len(values): raise InspectError('path not found')
            self.charge_work(1)
            return _project(values[index],path[1:],self)
        return self._container('sequence',_label(name),len(values),lambda index: ChildNode(PathSegment('index',index),str(index),self._child(values[index])))
    def map(self,path,entries,*,ordering='stable'):
        if ordering not in ('stable','unstable'): raise InspectError('invalid map ordering')
        if path:
            if path[0].kind!='map-key': raise InspectError('wrong path segment')
            missing=object();selected=missing
            for key,value in entries:
                self.charge_work(1)
                if key==path[0].value:
                    if selected is not missing: raise InspectError('invalid metadata')
                    selected=value
            if selected is missing: raise InspectError('path not found')
            return _project(selected,path[1:],self)
        def get(index):
            key,value=entries[index]
            key_bytes=_payload_bytes(key) if type(key) is str else _payload_bytes(key[1]) if type(key) is tuple and key[0]!='char' else 0
            if key_bytes>self.limits.max_value_bytes: raise InspectError('map key value limit')
            return ChildNode(PathSegment('map-key',key),'entry',self._child(value))
        return self._container('map',None,len(entries),get,ordering=ordering)


def _project(value,path,cx):
    # Custom adapters explicitly own schema/projection; no implicit model traversal.
    if hasattr(value,'inspect'):
        try:
            return value.inspect(path,cx)
        except InspectError:
            raise
        except Exception:
            # Host exception text is neither trusted metadata nor a control signal.
            raise InspectError('invalid adapter') from None
    if type(value) is str: return cx.string(path,value)
    if type(value) is bytes: return cx.byte_string(path,value)
    if type(value) is float:
        import struct
        # Python and Rust both produce shortest round-tripping f64 decimals;
        # Rust's Display expands exponents and omits an integral .0 suffix.
        decimal=repr(value)
        if decimal=='nan': decimal='NaN'
        elif 'e' in decimal:
            from decimal import Decimal
            decimal=format(Decimal(decimal),'f')
        if decimal.endswith('.0'): decimal=decimal[:-2]
        return cx.scalar(path,('f64',decimal,int.from_bytes(struct.pack('>d',value),'big')))
    if value is None or type(value) in (bool,int):
        kind='unit' if value is None else 'bool' if type(value) is bool else 'python-int'
        if type(value) is int:
            # Reject definitely oversized integers before decimal conversion; the
            # upper approximation to log2(10) must not reject values that fit.
            maximum=min(cx.limits.max_value_bytes,4096)
            digits=maximum-(value<0)
            if digits<1 or value.bit_length()>(digits*3322+999)//1000:
                return cx._leaf(path,InspectNode('truncated',completeness='value-limit'))
            decimal=str(value)
            if len(decimal)>maximum:
                return cx._leaf(path,InspectNode('truncated',completeness='value-limit'))
            return cx.scalar(path,(kind,decimal))
        return cx.scalar(path,(kind,value))
    if type(value) in (tuple,list): return cx.sequence(path,value)
    raise InspectError('no structural inspection adapter')


def _read_schema(value):
    if hasattr(value,'schema'):
        try: schema=value.schema()
        except InspectError: raise
        except Exception: raise InspectError('schema callback failed') from None
    else: schema=DisplaySchema(type(value).__name__)
    if type(schema) is not DisplaySchema: raise InspectError('invalid display schema')
    _label(schema.name)
    _source(schema.source)
    try: _natural(schema.version,'schema version',maximum=(1<<32)-1)
    except ValueError: raise InspectError('invalid display schema') from None
    return _copy(schema)


def inspect(value,query=InspectQuery()):
    if type(query) is not InspectQuery: raise ValueError('expected InspectQuery')
    if len(query.path)>query.limits.max_depth: raise InspectError('path too deep')
    if query.page.limit>query.limits.max_page_size: raise InspectError('invalid page')
    return _inspect_with_schema(value,query,_read_schema(value))


def _inspect_with_schema(value,query,schema):
    if type(query) is not InspectQuery: raise ValueError('expected InspectQuery')
    if len(query.path)>query.limits.max_depth: raise InspectError('path too deep')
    if query.page.limit>query.limits.max_page_size: raise InspectError('invalid page')
    if schema.version!=query.schema_version: raise InspectError('schema mismatch')
    if _payload_bytes(tuple(s.value for s in query.path))+32*len(query.path)>query.limits.max_bytes: raise InspectError('path budget exceeded')
    context=InspectContext(query.limits,query.page)
    context.bytes=128+_payload_bytes(schema.name)+_payload_bytes(schema.source)+_payload_bytes(tuple(s.value for s in query.path))+32*len(query.path)
    node=_project(value,query.path,context)
    root=node
    if type(node) is not InspectNode: raise InspectError('adapter must return InspectNode')
    # Recount output, including custom adapters that bypass cooperative charging.
    count=0; work=0; total=128+_payload_bytes(schema.name)+_payload_bytes(schema.source)+_payload_bytes(tuple(s.value for s in query.path))+32*len(query.path)
    if total>query.limits.max_bytes: raise InspectError('projection budget exceeded')
    pending=[(node,0)]
    while pending:
        node,depth=pending.pop(); count+=1;work+=1
        if type(node) is not InspectNode or type(node.children) is not tuple: raise InspectError('invalid projected node')
        _node_shape(node,query.limits.max_value_bytes)
        if node.kind in ('string','bytes','opaque') and _payload_bytes(node.value,query.limits.max_value_bytes)>query.limits.max_value_bytes:
            raise InspectError('projection value limit')
        scalar_parts=_float_parts(node.value) if node.kind=='scalar' else None
        scalar_value=scalar_parts[1] if scalar_parts is not None else node.value[1] if node.kind=='scalar' else None
        if node.kind=='scalar' and node.value[0] not in ('unit','bool','char') and _payload_bytes(scalar_value,query.limits.max_value_bytes)>query.limits.max_value_bytes:
            raise InspectError('projection value limit')
        if count>query.limits.max_nodes or (depth>query.limits.max_depth and not (depth==query.limits.max_depth+1 and node.kind=='truncated')): raise InspectError('projection budget exceeded')
        if len(node.children)>query.limits.max_nodes-count: raise InspectError('projection budget exceeded')
        work+=len(node.children)
        if work>query.limits.max_work: raise InspectError('work limit')
        if any(type(c) is not ChildNode or type(c.segment) is not PathSegment for c in node.children): raise InspectError('invalid projected child')
        for child in node.children: _label(child.label); _source(child.source)
        total+=128+_payload_bytes(node.value,query.limits.max_bytes)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value)+_payload_bytes(c.source) for c in node.children)
        if total>query.limits.max_bytes: raise InspectError('projection budget exceeded')
        pending.extend((c.node,depth+1) for c in node.children)
    return InspectResult(query.snapshot,schema,tuple(query.path),_copy(root))


def compare_nodes(before,after, *, max_chars=256*1024):
    """Compare complete nodes; opt into larger strings with max_chars."""
    if (before is not None and not before.complete_with_budget(max_chars)) or (after is not None and not after.complete_with_budget(max_chars)): return 'unknown'
    if before is None: return 'added' if after is not None else 'unknown'
    if after is None: return 'removed'
    return 'unchanged' if before==after else 'changed'


@dataclass(frozen=True)
class InspectDiff:
    before: SnapshotId
    after: SnapshotId
    path: tuple[PathSegment,...]
    kind: str
    before_completeness: str
    after_completeness: str


def diff(before,after, *, max_chars=256*1024):
    """Compare projections, using an explicit larger budget for large values."""
    compatible=before.schema==after.schema and before.path==after.path
    return InspectDiff(before.snapshot,after.snapshot,after.path,compare_nodes(before.node,after.node,max_chars=max_chars) if compatible else 'unknown',before.node.completeness,after.node.completeness)
