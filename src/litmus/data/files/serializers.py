"""Serialization registry for FileStore artifacts (build items 12 + 13).

A single source of truth for "how does this value land on disk?"
Both :meth:`FileStore.put` and the legacy ``save_ref_to_dir`` helper
use this registry so the dispatch logic lives in one place and the
MIME / extension convention is the same regardless of which file
layout the caller writes to.

Each registered handler carries:

- ``extension``: file extension including the dot (e.g. ``".npz"``)
- ``mime``: MIME type per the Litmus convention table (item 13)
- ``write(value, dest)``: callable that writes ``value`` to ``dest``

Built-in handlers (in priority order — first match wins):

- ``Path``         → copied via :func:`shutil.copy`; extension preserved
  from source (default ``.bin``); MIME defaults to
  ``application/octet-stream`` (the user is presumed to know the
  payload's real MIME and can override via ``attributes``)
- ``Waveform``     → ``.npz`` via ``np.savez``; MIME
  ``application/x-numpy-npz``. Falls back to ``.json`` + Pydantic
  dump when numpy isn't importable.
- ``bytes``        → ``.bin``; MIME ``application/octet-stream``
- Pydantic ``BaseModel`` (anything with ``model_dump_json``) →
  ``.json``; MIME ``application/json``
- numpy ``ndarray`` (anything with ``tolist`` + ``dtype``) →
  ``.npy`` via ``np.save``; MIME ``application/x-numpy-npy``.
  JSON fallback when numpy is unavailable.

Opportunistic handlers (registered only when the library is
importable):

- ``PIL.Image.Image`` → ``.png``; MIME ``image/png``
- ``pandas.DataFrame`` → ``.parquet``; MIME
  ``application/vnd.apache.parquet``

Custom types may either:

- expose a ``litmus_serialize(dest: Path) -> Path`` method (the
  serializer protocol — preferred when the object knows its own
  format), OR
- be registered explicitly via :func:`register_serializer`.

Pickle is the **last-resort fallback** for any value the registry
can't match. It emits a ``RuntimeWarning`` naming the type so
callers see what they should be registering a handler for; the
warning is what nudges the codebase toward typed serializers
rather than silent pickle bloat.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import pickle
import shutil
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from litmus.data.models import Waveform

try:
    HAS_NUMPY = _ilu.find_spec("numpy") is not None
except Exception:  # pragma: no cover - defensive
    HAS_NUMPY = False


class Serializer(NamedTuple):
    """One entry in the registry: how a value type lands on disk."""

    extension: str
    mime: str
    write: Callable[[Any, Path], None]


# The registry is a list of (predicate, serializer) pairs. Predicate is a
# callable so we can use ``isinstance`` checks (typical case) or arbitrary
# duck-typing (numpy-array detection without importing numpy at module
# load time). First match wins; user registrations are inserted at the
# front so they shadow built-ins.
_Registry = list[tuple[Callable[[Any], bool], Serializer]]
_registry: _Registry = []


# --------------------------------------------------------------------- #
# Built-in writers                                                      #
# --------------------------------------------------------------------- #


def _write_path(value: Any, dest: Path) -> None:
    shutil.copy(value, dest)


def _write_bytes(value: Any, dest: Path) -> None:
    dest.write_bytes(value)


def _write_basemodel_json(value: Any, dest: Path) -> None:
    dest.write_text(value.model_dump_json())


def _write_waveform(value: Waveform, dest: Path) -> None:
    if HAS_NUMPY:
        import numpy as np

        np.savez(dest, Y=value.Y, t0=value.t0, dt=value.dt, **value.attributes)
    else:
        dest.write_text(value.model_dump_json())


def _write_ndarray(value: Any, dest: Path) -> None:
    if HAS_NUMPY:
        import numpy as np

        np.save(dest, value)
    else:
        dest.write_text(json.dumps(value.tolist()))


def _write_pil_image(value: Any, dest: Path) -> None:
    value.save(dest, format="PNG")


def _write_pandas_dataframe(value: Any, dest: Path) -> None:
    value.to_parquet(dest)


def _write_pickle(value: Any, dest: Path) -> None:
    with open(dest, "wb") as f:
        pickle.dump(value, f)


# --------------------------------------------------------------------- #
# Built-in registration                                                 #
# --------------------------------------------------------------------- #


def _waveform_ext_and_mime() -> tuple[str, str]:
    """Waveform extension + MIME depends on numpy availability."""
    if HAS_NUMPY:
        return ".npz", "application/x-numpy-npz"
    return ".json", "application/json"


def _ndarray_ext_and_mime() -> tuple[str, str]:
    if HAS_NUMPY:
        return ".npy", "application/x-numpy-npy"
    return ".json", "application/json"


def _is_pil_image(value: Any) -> bool:
    # Duck-type without importing PIL at module load.
    if not hasattr(value, "save"):
        return False
    mod = type(value).__module__
    return mod.startswith("PIL.") or mod == "PIL"


def _is_pandas_dataframe(value: Any) -> bool:
    if not hasattr(value, "to_parquet"):
        return False
    mod = type(value).__module__
    return mod.startswith("pandas.")


def _is_ndarray(value: Any) -> bool:
    """True for numpy-array-like values without importing numpy."""
    return hasattr(value, "tolist") and hasattr(value, "dtype")


def _is_basemodel(value: Any) -> bool:
    """Pydantic BaseModel ducktype — any model with model_dump_json."""
    return hasattr(value, "model_dump_json")


def _register_builtins() -> None:
    """Register the built-in handlers in priority order.

    The order matters because :func:`find_serializer` returns the
    first match — types that are subclasses of more general types
    must come first.

    Predicate order:

    1. ``Path``         — distinct from bytes; copy-with-suffix
    2. ``Waveform``     — distinct from BaseModel (also a BaseModel
       subclass; needs to land as .npz, not .json)
    3. ``bytes``        — raw payload
    4. Pydantic         — any model_dump_json-capable object
    5. PIL ``Image``    — opportunistic, before ndarray
       (PIL.Image quacks like an array)
    6. pandas DataFrame — opportunistic
    7. numpy ``ndarray``— covers any tolist + dtype object
    """
    # Type-specific dispatch lives at the bottom of each predicate.
    waveform_ext, waveform_mime = _waveform_ext_and_mime()
    ndarray_ext, ndarray_mime = _ndarray_ext_and_mime()

    builtins: list[tuple[Callable[[Any], bool], Serializer]] = [
        (
            lambda v: isinstance(v, Path),
            Serializer(extension=".bin", mime="application/octet-stream", write=_write_path),
        ),
        (
            lambda v: isinstance(v, Waveform),
            Serializer(extension=waveform_ext, mime=waveform_mime, write=_write_waveform),
        ),
        (
            lambda v: isinstance(v, bytes),
            Serializer(extension=".bin", mime="application/octet-stream", write=_write_bytes),
        ),
        (
            _is_pil_image,
            Serializer(extension=".png", mime="image/png", write=_write_pil_image),
        ),
        (
            _is_pandas_dataframe,
            Serializer(
                extension=".parquet",
                mime="application/vnd.apache.parquet",
                write=_write_pandas_dataframe,
            ),
        ),
        (
            _is_basemodel,
            Serializer(extension=".json", mime="application/json", write=_write_basemodel_json),
        ),
        (
            _is_ndarray,
            Serializer(extension=ndarray_ext, mime=ndarray_mime, write=_write_ndarray),
        ),
    ]
    _registry.extend(builtins)


_register_builtins()


# --------------------------------------------------------------------- #
# Pickle fallback                                                       #
# --------------------------------------------------------------------- #


PICKLE_FALLBACK = Serializer(
    extension=".pkl",
    mime="application/x-python-pickle",
    write=_write_pickle,
)


# --------------------------------------------------------------------- #
# Public API                                                            #
# --------------------------------------------------------------------- #


def register_serializer(
    predicate: Callable[[Any], bool] | type,
    *,
    extension: str,
    mime: str,
    write: Callable[[Any, Path], None],
) -> None:
    """Register a custom serializer.

    User registrations are inserted at index 0 so they shadow the
    built-in handlers — call this to override how a built-in type
    serializes, or to add a new type entirely.

    Args:
        predicate: Either a Python ``type`` (matched via
            ``isinstance``) or a callable ``(value) -> bool`` that
            decides whether this serializer applies. Use the
            callable form when the type isn't importable at module
            load time (e.g. optional dependencies).
        extension: File extension including the dot (e.g. ``".npz"``).
        mime: MIME type per the Litmus convention table (item 13).
        write: Callable ``(value, dest)`` that writes ``value`` to
            the destination ``Path``.
    """
    if isinstance(predicate, type):
        cls = predicate
        match: Callable[[Any], bool] = lambda v, _cls=cls: isinstance(v, _cls)  # noqa: E731
    else:
        match = predicate
    _registry.insert(0, (match, Serializer(extension=extension, mime=mime, write=write)))


def find_serializer(value: Any) -> Serializer:
    """Return the serializer for ``value``; fall back to pickle with a warning.

    Lookup order:

    1. The ``litmus_serialize`` protocol — if the value exposes
       ``litmus_serialize(dest: Path) -> Path``, use it. The object
       owns its file format; the extension is read from the object's
       ``litmus_extension`` attribute (default ``".bin"``) and the
       MIME from ``litmus_mime`` (default ``application/octet-stream``).
    2. The registry — first-match across user registrations + built-ins.
    3. Pickle fallback with ``RuntimeWarning`` naming the type.
    """
    if _has_litmus_serialize_protocol(value):
        extension = getattr(value, "litmus_extension", ".bin")
        mime = getattr(value, "litmus_mime", "application/octet-stream")
        return Serializer(extension=extension, mime=mime, write=_write_protocol)

    for predicate, serializer in _registry:
        if predicate(value):
            return serializer

    warnings.warn(
        f"FileStore: no registered serializer for {type(value).__name__}; "
        "falling back to pickle. Register a handler via "
        "`from litmus.data.files import register_serializer` so the artifact "
        "lands as a typed file instead.",
        RuntimeWarning,
        stacklevel=3,
    )
    return PICKLE_FALLBACK


def _has_litmus_serialize_protocol(value: Any) -> bool:
    """True for objects that know how to write themselves."""
    return callable(getattr(value, "litmus_serialize", None))


def _write_protocol(value: Any, dest: Path) -> None:
    """Adapter for the ``litmus_serialize`` protocol."""
    value.litmus_serialize(dest)


def _reset_registry_for_tests() -> None:
    """Reset the registry to built-ins-only. Tests that register a
    custom serializer use this in teardown to avoid leaking state."""
    _registry.clear()
    _register_builtins()
