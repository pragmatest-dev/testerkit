"""Token-discipline regression for the cross-session ContextVar clobber.

A nested session's close must RESTORE the outer session's store binding, not null
it. Before token discipline, ``set_event_store(None)`` / ``set_channel_store(None)``
on a nested close wiped the *outer* binding to ``None`` — producing order-dependent
"no active session" failures (a ``connect()`` inside the pytest session clobbered the
pytest session's binding for every test after it).
"""

from __future__ import annotations

from litmus.execution._state import (
    get_channel_store,
    get_event_store,
    push_channel_store,
    push_event_store,
    reset_channel_store,
    reset_event_store,
)


def test_nested_push_reset_restores_outer_event_store():
    base = get_event_store()  # whatever the ambient pytest session holds
    outer = object()
    outer_tok = push_event_store(outer)
    try:
        assert get_event_store() is outer
        inner = object()
        inner_tok = push_event_store(inner)
        assert get_event_store() is inner
        reset_event_store(inner_tok)
        # The fix: the inner close RESTORES the outer binding, not None.
        assert get_event_store() is outer
    finally:
        reset_event_store(outer_tok)
    assert get_event_store() is base  # fully restored to the pre-test baseline


def test_nested_push_reset_restores_outer_channel_store():
    base = get_channel_store()
    outer = object()
    outer_tok = push_channel_store(outer)
    try:
        assert get_channel_store() is outer
        inner = object()
        inner_tok = push_channel_store(inner)
        assert get_channel_store() is inner
        reset_channel_store(inner_tok)
        assert get_channel_store() is outer
    finally:
        reset_channel_store(outer_tok)
    assert get_channel_store() is base
