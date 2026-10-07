# Stateless native ABI 1

This host-specific bundle contains `include/stateless.h`, a Swift module map,
the shared/static libraries under `lib/`, and the MIT license. Rebuild the engine
for other architectures. Do not mix headers and libraries from different ABI
versions; `stateless_abi_version()` must return 1.

Compile C code using `-I /path/to/native/include -L /path/to/native/lib -lstateless`.
On macOS/Linux, configure the runtime search path with
`-Wl,-rpath,/path/to/native/lib` or install the shared library using your platform's
normal application packaging. The shared library avoids manual Rust static
runtime/system-library link configuration. Windows consumers must use matching
MSVC artifacts; Windows native binding execution is not currently qualified.

Implement `StatelessDispatch` and pass a versioned `StatelessCallbacks` table to
`stateless_model_new`. Context remains caller-owned, outlives the model, and must
not hide mutable logical state. Calls are synchronous and thread-confined. Never
reenter the same model or unwind across the ABI. Use `stateless_buffer_assign`
to copy a response into the borrowed callback buffer; do not free that buffer.
Free every owned model/buffer exactly once. Returned data pointers become invalid
when their buffer is changed/freed.

The included `ABI.md` specifies callback packet framing,
operation numbers and statuses. The C header documents signatures and ownership.
Operations cover exact recording/replay and finite enumeration. Inspect report
termination and recorded limits; status 0 alone is not proof of graph exhaustion.
No foreign fuzzing, shrinking or live recorder API is exposed by ABI 1.
