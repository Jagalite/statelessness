"""Candidate synchronous bounded model encoding; logical bytes, not process RSS."""
from __future__ import annotations
import inspect
from .core import ModelError, U64_MAX, _call, _copy, _natural


class _Encoding:
    def __init__(self, data, maximum): self.data, self.maximum, self.failure = data, maximum, None


class EncodeBuffer:
    """Append-only bounded byte buffer. Callback leases expire on every exit.

    Construction clears caller bytearray logical length. No capacity guarantee,
    thread safety or protection against private/raw backing aliases is implied.
    """
    __slots__ = ("__encoding", "__parent", "__active")
    def __init__(self, destination: bytearray, maximum: int):
        if type(destination) is not bytearray: raise ValueError("destination must be bytearray")
        _natural(maximum, "maximum")
        destination.clear()
        self.__encoding, self.__parent, self.__active = _Encoding(destination, maximum), None, True

    @classmethod
    def _child(cls, parent):
        parent._require()
        child = object.__new__(cls)
        child.__encoding, child.__parent, child.__active = parent.__encoding, parent, True
        return child

    def _require(self):
        if not self.__active: raise ModelError("encode buffer lease has expired")
        if self.__parent is not None: self.__parent._require()
        if self.__encoding.failure is not None: raise self.__encoding.failure
        return self.__encoding

    def _fail(self, error):
        encoding = self.__encoding
        if encoding.failure is None:
            # Poison before formatting an application exception: __str__ itself
            # may fail, and an outer codec can catch that secondary exception.
            encoding.failure = ModelError("encoder callback failed")
            if isinstance(error, ModelError):
                encoding.failure = error
            else:
                try: encoding.failure = ModelError(str(error))
                except BaseException: pass
        return encoding.failure

    def _revoke(self):
        self.__active, self.__encoding, self.__parent = False, None, None

    def append(self, value):
        encoding = self._require()
        try:
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ModelError("encoded chunk must be bytes, bytearray or memoryview")
            view = memoryview(value)
            if not view.c_contiguous: raise ModelError("encoded view must be contiguous")
            view = view.cast("B")
            if len(encoding.data) + view.nbytes > encoding.maximum:
                raise ModelError("encoded blob exceeds byte limit")
            encoding.data.extend(view)
        except Exception as error:
            raise self._fail(error) from error

    extend_from_slice = append
    extend_from_vec = append
    extend = append

    def finish(self): self._require()
    def __len__(self): return len(self._require().data)
    def snapshot(self): return bytes(self._require().data)

    def framed(self, callback):
        encoding = self._require()
        start = len(encoding.data)
        self.append(b"\0" * 8)
        child = EncodeBuffer._child(self)
        try:
            result = callback(child)
            if result is not None:
                if inspect.iscoroutine(result) or inspect.isgenerator(result): result.close()
                raise ModelError("encoder must return None synchronously")
            child.finish()
            length = len(encoding.data) - start - 8
            encoding.data[start:start + 8] = length.to_bytes(8, "little")
        except BaseException as error:
            self._fail(error)
            raise
        finally:
            child._revoke()


def _dispatch_encode(model, kind, value, destination, maximum=U64_MAX, *, stage=None):
    stage = stage or "encode " + kind
    sink = EncodeBuffer._child(destination) if isinstance(destination, EncodeBuffer) else EncodeBuffer(destination, maximum)
    try:
        def invoke():
            hook = getattr(model, "encode_" + kind + "_into", None)
            if hook is not None:
                result = hook(_copy(value), sink)
                if result is not None:
                    if inspect.iscoroutine(result) or inspect.isgenerator(result): result.close()
                    raise sink._fail(ModelError("encoder must return None synchronously"))
            else:
                callback = getattr(model, "encode_" + kind, None)
                if callback is None: raise ModelError("model lacks encode_" + kind + " or its into hook")
                result = callback(_copy(value))
                if type(result) is not bytes: raise ModelError("codec must return bytes")
                sink.append(result)
            sink.finish()
        try:
            _call(stage, invoke)
        except BaseException as error:
            sink._fail(error)
            raise
    finally:
        sink._revoke()


def encode_value(model, kind, value, *, maximum=U64_MAX, storage=None, stage=None):
    storage = bytearray() if storage is None else storage
    _dispatch_encode(model, kind, value, storage, maximum, stage=stage)
    return bytes(storage)
