"""Optional passive legacy trace cursor and explicit decode/verification APIs."""
from .core import ModelError, _natural, _call, _copy
from .debugger import EvidenceViewer
from .debugger_workbench import artifact_identity, fork_verified
from .evidence import Artifact

class TraceViewer:
    """Passive legacy trace cursor. Decoding and verification are explicit calls."""
    def __init__(self,trace,*,limits=None):
        from .evidence import EvidenceLimits
        self._viewer=EvidenceViewer(Artifact(trace),limits=EvidenceLimits() if limits is None else limits)
        self._position=0
        self._identity=artifact_identity(trace)
    @classmethod
    def read(cls,reader,*,limits=None):
        from .evidence import EvidenceLimits
        limits=EvidenceLimits() if limits is None else limits
        artifact=Artifact.read(reader,limits=limits)
        if artifact.version!=1: raise ModelError('TraceViewer requires legacy v1; use EvidenceViewer for v2')
        return cls(artifact.trace,limits=limits)
    @property
    def trace(self): return self._viewer.artifact.trace
    @property
    def identity(self): return self._identity
    @property
    def position(self): return self._position
    @property
    def retained_range(self): return self._viewer.retained_range
    def seek(self,sequence):
        _natural(sequence,'sequence')
        if self._viewer.boundary(sequence) is None: raise ModelError('snapshot outside retained history')
        self._position=sequence
    def state_bytes(self,maximum=4096):
        return self._viewer.preview(self._position,maximum)
    def checks(self): return self._viewer.boundary(self._position).checks
    def snapshot_bytes(self,sequence):
        boundary=self._viewer.boundary(sequence)
        if boundary is None: raise ModelError('snapshot outside retained history')
        return boundary.after
    def outputs(self,offset=0,limit=100,max_bytes_each=4096):
        from .debugger import EncodedPreview
        _natural(offset,'output offset'); _natural(limit,'output limit',1,1000); _natural(max_bytes_each,'output preview bytes',maximum=4096)
        outputs=self._viewer.boundary(self._position).outputs
        if offset>len(outputs): raise ModelError('output offset outside snapshot')
        end=min(len(outputs),offset+limit)
        return tuple(EncodedPreview(b[:max_bytes_each],len(b),len(b)<=max_bytes_each) for b in outputs[offset:end]),end==len(outputs)
    def decode_state(self,model,sequence):
        if _call('snapshot decoder metadata',model.metadata)!=self.trace.metadata:
            raise ModelError('snapshot decoder identity differs; verify selected build before branching')
        return _copy(_call('decode snapshot',model.decode_state,self.snapshot_bytes(sequence)))
    def verify(self,model,**kwargs): return self._viewer.verify(model,**kwargs)
    def fork_verified(self,id,model,sequence,policy,**kwargs): return fork_verified(self._viewer,id,model,sequence,policy,**kwargs)
