"""Optional structural display projections, never replay or equality keys.

Adapters must be side-effect-free and cooperative. Budgets charge owned logical
projection payloads and traversal, not application allocations or process RSS.
No implicit reflection, repr(), or codec decoding is used.
"""
from dataclasses import dataclass
from .core import ModelError, _natural, _copy, _call


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
        if self.completeness != 'complete' or self.kind in ('opaque','redacted','unavailable','truncated'): return False
        if self.kind == 'map' and self.ordering != 'stable': return False
        if self.kind in ('string','bytes') and _payload_bytes(self.value) != self.total_bytes: return False
        if self.child_count is not None and self.child_count != len(self.children): return False
        if self.page is not None and (self.page.offset != 0 or self.page.returned != len(self.children) or self.page.total != len(self.children) or self.page.next_offset is not None): return False
        return all(c.node.is_complete for c in self.children)


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
    kind: str
    decimal: str
    bits: int
    def __post_init__(self):
        if self.kind not in ('f32','f64'): raise ValueError('invalid float type')
        _label(self.decimal); _natural(self.bits,'float bits',maximum=(1<<int(self.kind[1:]))-1)
    def inspect(self,path,cx): return cx.scalar(path,(self.kind,self.decimal,self.bits))


class InspectError(ModelError): pass


def _label(value):
    if type(value) is not str: raise InspectError('metadata must be str')
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
    text=value[1:] if value.startswith('-') else value
    if not text or not text.isascii() or not text.isdigit() or (len(text)>1 and text[0]=='0') or value=='-0': raise InspectError('invalid integer scalar')
    if kind=='python-int': return
    if kind not in ('i8','i16','i32','i64','i128','isize','u8','u16','u32','u64','u128','usize'): raise InspectError('invalid integer type')
    # Native pointer-width identity is explicit; Python's qualified host width.
    import struct
    width=struct.calcsize('P')*8 if kind in ('isize','usize') else int(kind[1:])
    number=int(value)
    minimum=-(1<<(width-1)) if kind.startswith('i') else 0
    maximum=(1<<(width-1))-1 if kind.startswith('i') else (1<<width)-1
    if number<minimum or number>maximum: raise InspectError('integer scalar out of range')


def _key(value):
    if type(value) is str: _label(value); return
    if type(value) is bool: return
    if type(value) is tuple and len(value)==2:
        if value[0]=='char': CharView(value[1]); return
        _integer(value[0],value[1]); return
    raise InspectError('invalid typed map key')


def _node_shape(node):
    if node.kind=='scalar':
        if type(node.value) is not tuple or len(node.value) not in (2,3): raise InspectError('invalid scalar')
        kind,value=node.value[:2]
        if kind in ('f32','f64'):
            if len(node.value)!=3: raise InspectError('invalid float scalar')
            FloatView(kind,value,node.value[2]); return
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
    elif node.kind in ('object','sequence','opaque'): _label(node.value)
    elif node.kind=='bytes':
        if type(node.value) is not bytes: raise InspectError('invalid bytes projection')
    elif node.kind=='enum':
        if type(node.value) is not tuple or len(node.value)!=3: raise InspectError('invalid enum metadata')
        for value in node.value: _label(value)


def _payload_bytes(value):
    if type(value) is str:
        if len(value)>256*1024: raise InspectError('projection value limit')
        return len(value.encode('utf8'))
    if type(value) is bytes: return len(value)
    if type(value) in (tuple,list): return sum(_payload_bytes(v) for v in value)
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
        size=128+_payload_bytes(node.value)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value) for c in node.children)
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
            encoded=character.encode('utf8'); total+=len(encoded)
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
        # Rust opaque labels use a UTF-8 boundary preserving byte preview.
        try: encoded=label.encode('utf8')
        except UnicodeEncodeError as error: raise InspectError('metadata must be UTF-8') from error
        label=encoded[:self.limits.max_value_bytes].decode('utf8',errors='ignore')
        return self._leaf(path,InspectNode('opaque',_label(label),completeness='opaque'))
    def _child(self,value):
        if self.depth >= self.limits.max_depth: return self._node(InspectNode('truncated',completeness='depth'))
        old_page,self.page=self.page,PageRequest(0,self.page.limit)
        self.depth += 1
        try: return _project(value,(),self)
        finally: self.depth -= 1; self.page=old_page
    def _container(self,kind,value,total,get,*,ordering=None):
        self.charge_work(1)
        offset=self.page.offset
        if offset>total: raise InspectError('page outside collection')
        end=min(total,offset+self.page.limit)
        children=[]
        for index in range(offset,end): self.charge_work(1); children.append(get(index))
        page=PageInfo(offset,len(children),total,end if end<total else None)
        completeness='complete' if offset==0 and end==total else 'pagination'
        return self._node(InspectNode(kind,value,tuple(children),total,page,completeness,ordering=ordering))
    def object(self,path,name,fields):
        name=_label(name)
        if path:
            if path[0].kind!='field': raise InspectError('wrong path segment')
            for field in fields:
                self.charge_work(1)
                if field.id==path[0].value:
                    return self.redacted(path[1:]) if field.hidden else _project(field.value,path[1:],self)
            raise InspectError('path not found')
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
            for key,value in entries:
                self.charge_work(1)
                if key==path[0].value: return _project(value,path[1:],self)
            raise InspectError('path not found')
        def get(index):
            key,value=entries[index]
            key_bytes=_payload_bytes(key) if type(key) is str else _payload_bytes(key[1]) if type(key) is tuple and key[0]!='char' else 0
            if key_bytes>self.limits.max_value_bytes: raise InspectError('map key value limit')
            return ChildNode(PathSegment('map-key',key),'entry',self._child(value))
        return self._container('map',None,len(entries),get,ordering=ordering)


def _project(value,path,cx):
    # Custom adapters explicitly own schema/projection; no implicit model traversal.
    if hasattr(value,'inspect'): return _call('inspect adapter',value.inspect,path,cx)
    if type(value) is str: return cx.string(path,value)
    if type(value) is bytes: return cx.byte_string(path,value)
    if type(value) is float:
        import struct
        return cx.scalar(path,('f64',format(value,'.17g'),int.from_bytes(struct.pack('>d',value),'big')))
    if value is None or type(value) in (bool,int):
        kind='unit' if value is None else 'bool' if type(value) is bool else 'python-int'
        if type(value) is int:
            # Conservative bit bound before decimal conversion allocates digits.
            if value.bit_length()>max(1,cx.limits.max_value_bytes-1)*3:
                return cx._leaf(path,InspectNode('truncated',completeness='value-limit'))
        return cx.scalar(path,(kind,str(value) if type(value) is int else value))
    if type(value) in (tuple,list): return cx.sequence(path,value)
    raise InspectError('no structural inspection adapter')


def inspect(value,query=InspectQuery()):
    if type(query) is not InspectQuery: raise ValueError('expected InspectQuery')
    if len(query.path)>query.limits.max_depth: raise InspectError('path too deep')
    if query.page.limit>query.limits.max_page_size: raise InspectError('invalid page')
    schema=_call('display schema',value.schema) if hasattr(value,'schema') else DisplaySchema(type(value).__name__)
    if type(schema) is not DisplaySchema: raise InspectError('invalid display schema')
    _label(schema.name)
    _source(schema.source)
    if schema.version!=query.schema_version: raise InspectError('schema mismatch')
    if _payload_bytes(tuple(s.value for s in query.path))+32*len(query.path)>query.limits.max_bytes: raise InspectError('path budget exceeded')
    context=InspectContext(query.limits,query.page)
    node=_project(value,query.path,context)
    root=node
    if type(node) is not InspectNode: raise InspectError('adapter must return InspectNode')
    # Recount output, including custom adapters that bypass cooperative charging.
    count=0; total=128+_payload_bytes(schema.name)+_payload_bytes(schema.source)+_payload_bytes(tuple(s.value for s in query.path))+32*len(query.path)
    if total>query.limits.max_bytes: raise InspectError('projection budget exceeded')
    pending=[(node,0)]
    while pending:
        node,depth=pending.pop(); count+=1
        if type(node) is not InspectNode or type(node.children) is not tuple: raise InspectError('invalid projected node')
        _node_shape(node)
        if count>query.limits.max_nodes or (depth>query.limits.max_depth and not (depth==query.limits.max_depth+1 and node.kind=='truncated')): raise InspectError('projection budget exceeded')
        if len(node.children)>query.limits.max_nodes-count: raise InspectError('projection budget exceeded')
        if any(type(c) is not ChildNode or type(c.segment) is not PathSegment for c in node.children): raise InspectError('invalid projected child')
        for child in node.children: _label(child.label); _source(child.source)
        total+=128+_payload_bytes(node.value)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value)+_payload_bytes(c.source) for c in node.children)
        if total>query.limits.max_bytes: raise InspectError('projection budget exceeded')
        pending.extend((c.node,depth+1) for c in node.children)
    return InspectResult(query.snapshot,schema,tuple(query.path),_copy(root))


def compare_nodes(before,after):
    if (before is not None and not before.is_complete) or (after is not None and not after.is_complete): return 'unknown'
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


def diff(before,after):
    compatible=before.schema==after.schema and before.path==after.path
    return InspectDiff(before.snapshot,after.snapshot,after.path,compare_nodes(before.node,after.node) if compatible else 'unknown',before.node.completeness,after.node.completeness)
