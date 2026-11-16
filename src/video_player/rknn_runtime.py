"""Minimal ctypes binding for Rockchip's ``librknnrt`` runtime (aarch64 board).

The board does not ship a Python wheel for the RKNN runtime (Python 3.13 has no
``rknn-toolkit-lite2`` build, see ``docs/BOARD.md``), so M2 talks to
``/usr/lib/librknnrt.so`` through the C API declared in ``vendor/rknn_api.h``.

Only the subset needed for single-input, multi-output inference is wrapped:
``rknn_init``, ``rknn_query``, ``rknn_inputs_set``, ``rknn_run``,
``rknn_outputs_get`` and ``rknn_outputs_release``.  Tensors are copied into
NumPy arrays, which keeps the rest of the player free of ctypes.
"""

from __future__ import annotations

import ctypes as C
from pathlib import Path
from typing import Mapping

import numpy as np

# --- constants (vendor/rknn_api.h) -----------------------------------------

RKNN_SUCC = 0
RKNN_QUERY_IN_OUT_NUM = 0
RKNN_QUERY_INPUT_ATTR = 1
RKNN_QUERY_OUTPUT_ATTR = 2
RKNN_TENSOR_FLOAT32 = 0
RKNN_TENSOR_FLOAT16 = 1
RKNN_TENSOR_UINT8 = 3
RKNN_TENSOR_NCHW = 0
RKNN_TENSOR_NHWC = 1
RKNN_NPU_CORE_AUTO = 0
RKNN_NPU_CORE_0 = 1
RKNN_NPU_CORE_0_1_2 = 7

RKNN_MAX_DIMS = 16
RKNN_MAX_NAME_LEN = 256

_LIBRARY_CANDIDATES = (
    "librknnrt.so",
    "/usr/lib/librknnrt.so",
    "/usr/local/lib/librknnrt.so",
)


class RknnError(RuntimeError):
    """Raised when ``librknnrt`` returns a non-zero status code."""

    def __init__(self, operation: str, code: int) -> None:
        super().__init__(f"{operation} failed with RKNN error code {code}")
        self.operation = operation
        self.code = code


class _RknnTensorAttr(C.Structure):
    _fields_ = [
        ("index", C.c_uint32),
        ("n_dims", C.c_uint32),
        ("dims", C.c_uint32 * RKNN_MAX_DIMS),
        ("name", C.c_char * RKNN_MAX_NAME_LEN),
        ("n_elems", C.c_uint32),
        ("size", C.c_uint32),
        ("fmt", C.c_int),
        ("type", C.c_int),
        ("qnt_type", C.c_int),
        ("fl", C.c_int8),
        ("zp", C.c_int32),
        ("scale", C.c_float),
        ("w_stride", C.c_uint32),
        ("size_with_stride", C.c_uint32),
        ("pass_through", C.c_uint8),
        ("h_stride", C.c_uint32),
    ]


class _RknnInput(C.Structure):
    _fields_ = [
        ("index", C.c_uint32),
        ("buf", C.c_void_p),
        ("size", C.c_uint32),
        ("pass_through", C.c_uint8),
        ("type", C.c_int),
        ("fmt", C.c_int),
    ]


class _RknnOutput(C.Structure):
    _fields_ = [
        ("want_float", C.c_uint8),
        ("is_prealloc", C.c_uint8),
        ("index", C.c_uint32),
        ("buf", C.c_void_p),
        ("size", C.c_uint32),
    ]


class _RknnInOutNum(C.Structure):
    _fields_ = [("n_input", C.c_uint32), ("n_output", C.c_uint32)]


def _load_library() -> C.CDLL:
    last_error: OSError | None = None
    for candidate in _LIBRARY_CANDIDATES:
        try:
            return C.CDLL(candidate)
        except OSError as exc:  # pragma: no cover - board only
            last_error = exc
    raise RuntimeError(
        "librknnrt.so not found; install the RKNN runtime on the board "
        f"(tried {', '.join(_LIBRARY_CANDIDATES)})"
    ) from last_error


def _configure(lib: C.CDLL) -> None:
    lib.rknn_init.argtypes = [
        C.POINTER(C.c_uint64),
        C.c_void_p,
        C.c_uint32,
        C.c_uint32,
        C.c_void_p,
    ]
    lib.rknn_init.restype = C.c_int
    lib.rknn_query.argtypes = [
        C.c_uint64,
        C.c_int,
        C.c_void_p,
        C.c_uint32,
    ]
    lib.rknn_query.restype = C.c_int
    lib.rknn_inputs_set.argtypes = [C.c_uint64, C.c_uint32, C.POINTER(_RknnInput)]
    lib.rknn_inputs_set.restype = C.c_int
    lib.rknn_run.argtypes = [C.c_uint64, C.c_void_p]
    lib.rknn_run.restype = C.c_int
    lib.rknn_outputs_get.argtypes = [
        C.c_uint64,
        C.c_uint32,
        C.POINTER(_RknnOutput),
        C.c_void_p,
    ]
    lib.rknn_outputs_get.restype = C.c_int
    lib.rknn_outputs_release.argtypes = [C.c_uint64, C.c_uint32, C.POINTER(_RknnOutput)]
    lib.rknn_outputs_release.restype = C.c_int
    lib.rknn_set_core_mask.argtypes = [C.c_uint64, C.c_int]
    lib.rknn_set_core_mask.restype = C.c_int
    lib.rknn_destroy.argtypes = [C.c_uint64]
    lib.rknn_destroy.restype = C.c_int


class RknnRuntime:
    """Single-input, multi-output RKNN model running on the board NPU."""

    def __init__(self, model_path: str | Path, core_mask: int = RKNN_NPU_CORE_AUTO) -> None:
        self._lib = _load_library()
        _configure(self._lib)
        self._context = C.c_uint64(0)
        self._model_bytes = Path(model_path).read_bytes()
        self._model_buffer = C.create_string_buffer(self._model_bytes, len(self._model_bytes))
        code = self._lib.rknn_init(
            C.byref(self._context),
            C.cast(self._model_buffer, C.c_void_p),
            len(self._model_bytes),
            0,
            None,
        )
        if code != RKNN_SUCC:
            raise RknnError("rknn_init", code)
        if core_mask != RKNN_NPU_CORE_AUTO:
            self._lib.rknn_set_core_mask(self._context, core_mask)

        self.input_attrs = self._query_attrs(RKNN_QUERY_INPUT_ATTR)
        self.output_attrs = self._query_attrs(RKNN_QUERY_OUTPUT_ATTR)
        self.input_names = [attr["name"] for attr in self.input_attrs]
        self.output_names = [attr["name"] for attr in self.output_attrs]

    # -- public API ---------------------------------------------------------

    def infer(self, input_nhwc: np.ndarray) -> dict[str, np.ndarray]:
        """Run one inference on a NHWC ``uint8`` tensor and copy outputs."""

        if input_nhwc.dtype != np.uint8:
            raise TypeError(f"expected uint8 input, got {input_nhwc.dtype}")
        if not input_nhwc.flags["C_CONTIGUOUS"]:
            input_nhwc = np.ascontiguousarray(input_nhwc)

        entry = _RknnInput()
        entry.index = 0
        entry.buf = input_nhwc.ctypes.data_as(C.c_void_p)
        entry.size = input_nhwc.nbytes
        entry.pass_through = 0
        entry.type = RKNN_TENSOR_UINT8
        entry.fmt = RKNN_TENSOR_NHWC
        code = self._lib.rknn_inputs_set(self._context, 1, C.byref(entry))
        if code != RKNN_SUCC:
            raise RknnError("rknn_inputs_set", code)

        code = self._lib.rknn_run(self._context, None)
        if code != RKNN_SUCC:
            raise RknnError("rknn_run", code)

        count = len(self.output_attrs)
        outputs = (_RknnOutput * count)()
        for index in range(count):
            outputs[index].index = index
            outputs[index].want_float = 1
            outputs[index].is_prealloc = 0
        code = self._lib.rknn_outputs_get(self._context, count, outputs, None)
        if code != RKNN_SUCC:
            raise RknnError("rknn_outputs_get", code)

        result: dict[str, np.ndarray] = {}
        try:
            for index, attr in enumerate(self.output_attrs):
                size = int(outputs[index].size)
                n_elems = int(attr["n_elems"]) or size // 4
                buffer = C.string_at(outputs[index].buf, size)
                array = np.frombuffer(buffer, dtype=np.float32, count=n_elems).copy()
                dims = tuple(int(d) for d in attr["dims"][: attr["n_dims"]])
                if dims and int(np.prod(dims)) == array.size:
                    array = array.reshape(dims)
                result[self._output_key(index, attr)] = array
        finally:
            self._lib.rknn_outputs_release(self._context, count, outputs)
        return result

    def close(self) -> None:
        if getattr(self, "_context", None) is not None and self._context.value:
            self._lib.rknn_destroy(self._context)
            self._context.value = 0

    def __enter__(self) -> "RknnRuntime":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _query_attrs(self, command: int) -> list[dict[str, object]]:
        counts = _RknnInOutNum()
        code = self._lib.rknn_query(
            self._context,
            RKNN_QUERY_IN_OUT_NUM,
            C.byref(counts),
            C.sizeof(counts),
        )
        if code != RKNN_SUCC:
            raise RknnError("rknn_query(IN_OUT_NUM)", code)
        number = counts.n_input if command == RKNN_QUERY_INPUT_ATTR else counts.n_output
        attrs: list[dict[str, object]] = []
        for index in range(number):
            attr = _RknnTensorAttr()
            attr.index = index
            code = self._lib.rknn_query(
                self._context, command, C.byref(attr), C.sizeof(attr)
            )
            if code != RKNN_SUCC:
                raise RknnError(f"rknn_query({'input' if command == RKNN_QUERY_INPUT_ATTR else 'output'})", code)
            name = attr.name.decode("utf-8", "replace").split("\x00", 1)[0]
            attrs.append(
                {
                    "index": index,
                    "name": name,
                    "n_dims": int(attr.n_dims),
                    "dims": list(attr.dims),
                    "n_elems": int(attr.n_elems),
                    "size": int(attr.size),
                    "fmt": int(attr.fmt),
                    "type": int(attr.type),
                }
            )
        return attrs

    def _output_key(self, index: int, attr: Mapping[str, object]) -> str:
        name = str(attr.get("name") or "")
        return name if name else f"output_{index}"
