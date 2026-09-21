"""Tests for data retention utilities."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from testerkit.data.retention import parse_duration, prune_all, prune_date_dirs


class TestParseDuration:
    def test_valid(self):
        assert parse_duration("30d") == timedelta(days=30)
        assert parse_duration("90d") == timedelta(days=90)
        assert parse_duration(" 7d ") == timedelta(days=7)

    def test_invalid(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("30h")
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("abc")


class TestPruneDateDirs:
    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a project dir with testerkit.yaml so prune considers it owned."""
        (tmp_path / "testerkit.yaml").write_text(f"name: test\ndata_dir: {tmp_path / 'data'}\n")
        monkeypatch.chdir(tmp_path)
        return tmp_path / "data"

    def _make_date_dirs(self, base: Path, dates: list[date]) -> None:
        for d in dates:
            (base / d.isoformat()).mkdir(parents=True)

    def test_deletes_old_keeps_recent(self, project_dir: Path):
        today = date.today()
        old = today - timedelta(days=60)
        recent = today - timedelta(days=5)
        self._make_date_dirs(project_dir, [old, recent])

        cutoff = today - timedelta(days=30)
        removed = prune_date_dirs(project_dir, cutoff)

        assert len(removed) == 1
        assert removed[0].name == old.isoformat()
        assert not (project_dir / old.isoformat()).exists()
        assert (project_dir / recent.isoformat()).exists()

    def test_dry_run(self, project_dir: Path):
        old = date.today() - timedelta(days=60)
        self._make_date_dirs(project_dir, [old])

        cutoff = date.today() - timedelta(days=30)
        removed = prune_date_dirs(project_dir, cutoff, dry_run=True)

        assert len(removed) == 1
        assert (project_dir / old.isoformat()).exists()  # not deleted

    def test_nonexistent_dir(self, project_dir: Path):
        removed = prune_date_dirs(project_dir / "nope", date.today())
        assert removed == []

    def test_ignores_non_date_dirs(self, project_dir: Path):
        (project_dir / "not-a-date").mkdir(parents=True)
        removed = prune_date_dirs(project_dir, date.today())
        assert removed == []

    def test_refuses_unowned_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        no_project = tmp_path / "no_project"
        no_project.mkdir()
        monkeypatch.chdir(no_project)

        with pytest.raises(PermissionError, match="project-owned"):
            prune_date_dirs(tmp_path / "whatever", date.today())


class TestPruneAll:
    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a project dir with testerkit.yaml so prune considers it owned."""
        (tmp_path / "testerkit.yaml").write_text(f"name: test\ndata_dir: {tmp_path / 'data'}\n")
        monkeypatch.chdir(tmp_path)
        return tmp_path / "data"

    def _seg(self, channels_dir: Path, date_str: str, channel_id: str, sess: str) -> Path:
        d = channels_dir / date_str
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{channel_id}_{sess}.arrow"
        p.write_bytes(b"seg")
        return p

    def test_prunes_unreferenced_channels_and_event_dirs(self, project_dir: Path):
        old = (date.today() - timedelta(days=60)).isoformat()
        # No runs/ dir → nothing is referenced → the old segment ages out.
        seg = self._seg(project_dir / "channels", old, "scope.ch1", "abcdef12")
        (project_dir / "events" / old).mkdir(parents=True)

        result = prune_all(project_dir, "30d")
        # channel segment pruned (unreferenced); its now-empty date dir cleaned up
        assert seg in result["channels"]
        assert not seg.exists()
        assert not (project_dir / "channels" / old).exists()
        # event date dir pruned (whole-dir, as before)
        assert len(result["events"]) == 1
        assert not (project_dir / "events" / old).exists()

    def test_ref_aware_pins_referenced_channel(
        self, project_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        old = (date.today() - timedelta(days=60)).isoformat()
        ch = project_dir / "channels"
        kept = self._seg(ch, old, "scope.ch1", "aaaaaaaa")  # referenced → pinned
        gone = self._seg(ch, old, "scope.ch2", "bbbbbbbb")  # unreferenced → pruned

        # A run references (scope.ch1, aaaaaaaa): it's evidence, must be kept.
        monkeypatch.setattr(
            "testerkit.data.retention._referenced_pairs",
            lambda *_a: {("scope.ch1", "aaaaaaaa")},
        )
        result = prune_all(project_dir, "30d", data_types=("channels",))

        assert kept.exists()  # pinned — no copy, the channel:// ref stays valid
        assert not gone.exists()  # unreferenced — aged out
        assert gone in result["channels"] and kept not in result["channels"]
        assert (ch / old).exists()  # date dir retained (still holds the pinned slice)

    def test_recent_channels_untouched(self, project_dir: Path):
        recent = (date.today() - timedelta(days=5)).isoformat()
        seg = self._seg(project_dir / "channels", recent, "scope.ch1", "abcdef12")
        result = prune_all(project_dir, "30d", data_types=("channels",))
        assert seg.exists()
        assert result["channels"] == []

    def test_refuses_unowned_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Pruning a dir not owned by any project should fail."""
        # chdir to a dir with no testerkit.yaml
        no_project = tmp_path / "no_project"
        no_project.mkdir()
        monkeypatch.chdir(no_project)

        target = tmp_path / "some_random_dir"
        target.mkdir()
        with pytest.raises(PermissionError, match="project-owned"):
            prune_all(target, "30d")


class TestFilesRefAware:
    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (tmp_path / "testerkit.yaml").write_text(f"name: test\ndata_dir: {tmp_path / 'data'}\n")
        monkeypatch.chdir(tmp_path)
        return tmp_path / "data"

    def test_pins_referenced_prunes_orphan(self, project_dir: Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        old = (date.today() - timedelta(days=60)).isoformat()
        sid = "abcdef1234567890"

        # A run parquet referencing files/{old}/{sid}/kept.bin.
        runs = project_dir / "runs" / "runs" / old
        runs.mkdir(parents=True)
        pq.write_table(
            pa.table({"out_x": [f"file://{old}/{sid}/kept.bin"]}),
            runs / "120000_UUT.parquet",
        )

        fdir = project_dir / "files" / old / sid
        fdir.mkdir(parents=True)
        kept = fdir / "kept.bin"
        kept.write_bytes(b"keep")
        (fdir / "kept.bin.meta.json").write_text("{}")
        orphan = fdir / "orphan.bin"
        orphan.write_bytes(b"gone")
        (fdir / "orphan.bin.meta.json").write_text("{}")

        result = prune_all(project_dir, "30d", data_types=("files",))

        assert kept.exists()  # pinned — a run references it (no copy)
        assert (fdir / "kept.bin.meta.json").exists()
        assert not orphan.exists()  # unreferenced orphan aged out
        assert not (fdir / "orphan.bin.meta.json").exists()  # sidecar went with it
        assert orphan in result["files"]

    def test_recent_files_untouched(self, project_dir: Path) -> None:
        recent = (date.today() - timedelta(days=5)).isoformat()
        fdir = project_dir / "files" / recent / "sess"
        fdir.mkdir(parents=True)
        f = fdir / "x.bin"
        f.write_bytes(b"x")
        result = prune_all(project_dir, "30d", data_types=("files",))
        assert f.exists()
        assert result["files"] == []

    def test_ext_filter_prunes_only_matching_type(self, project_dir: Path) -> None:
        old = (date.today() - timedelta(days=60)).isoformat()
        fdir = project_dir / "files" / old / "sess"
        fdir.mkdir(parents=True)
        tdms = fdir / "raw.tdms"
        tdms.write_bytes(b"raw")
        png = fdir / "shot.png"
        png.write_bytes(b"img")

        # No runs/ → both unreferenced; --ext tdms ages out the raw, keeps the image.
        result = prune_all(project_dir, "30d", data_types=("files",), exts=frozenset({"tdms"}))
        assert not tdms.exists()  # matched the type filter → pruned
        assert png.exists()  # other type → kept (tiered retention)
        assert tdms in result["files"]


class TestEventsForwardAwareRetention:
    """docs/36 P2; testerkit-server docs/06 F10 — "a pruned, not-yet-forwarded
    segment is silent loss". Real WAL segments (not the empty-dir shortcut
    the other tests use) + a real forwarder cursor file, in the exact
    on-disk shape ``forward_cmd._save_cursor`` writes."""

    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (tmp_path / "testerkit.yaml").write_text(f"name: test\ndata_dir: {tmp_path / 'data'}\n")
        monkeypatch.chdir(tmp_path)
        return tmp_path / "data"

    def _wal_segment(
        self, events_dir: Path, date_str: str, *, writer_key: str, offsets: list[int]
    ) -> None:
        from typing import Any

        import pyarrow as pa
        import pyarrow.ipc as ipc

        from testerkit.replication import EVENT_WAL_SCHEMA

        n = len(offsets)
        data: dict[str, list[Any]] = {name: [None] * n for name in EVENT_WAL_SCHEMA.names}
        data["id"] = [f"e{i}" for i in offsets]
        data["event_type"] = ["test.measurement"] * n
        data["session_id"] = ["s1"] * n
        data["writer_key"] = [writer_key] * n
        data["event_offset"] = offsets
        data["json"] = ["{}"] * n
        table = pa.table(data, schema=EVENT_WAL_SCHEMA)
        seg_dir = events_dir / date_str
        seg_dir.mkdir(parents=True, exist_ok=True)
        seg = seg_dir / f"seg-{writer_key}.arrow"
        with pa.OSFile(str(seg), "wb") as sink, ipc.new_stream(sink, EVENT_WAL_SCHEMA) as w:
            w.write_table(table)

    def _save_cursor(self, path: Path, cursor: dict[str, int]) -> None:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cursor))

    def test_unforwarded_date_dir_is_pinned_not_pruned(self, project_dir: Path) -> None:
        old = (date.today() - timedelta(days=60)).isoformat()
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, old, writer_key="w0", offsets=[0, 1, 2])
        # No cursor file at all — the safe default: nothing known-forwarded.

        result = prune_all(project_dir, "30d", data_types=("events",))
        assert result["events"] == []
        assert (events_dir / old).exists()  # pinned

    def test_forwarded_date_dir_is_pruned(self, project_dir: Path) -> None:
        old = (date.today() - timedelta(days=60)).isoformat()
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, old, writer_key="w0", offsets=[0, 1, 2])
        self._save_cursor(events_dir / "_forward_cursor.json", {"w0": 2})  # every offset covered

        result = prune_all(project_dir, "30d", data_types=("events",))
        assert (events_dir / old) in result["events"]
        assert not (events_dir / old).exists()

    def test_partially_forwarded_date_dir_is_pinned(self, project_dir: Path) -> None:
        """The cursor covers offset 0 but the segment goes up to 2 — still
        pinned (F10: never prune ahead of the true high-water mark)."""
        old = (date.today() - timedelta(days=60)).isoformat()
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, old, writer_key="w0", offsets=[0, 1, 2])
        self._save_cursor(events_dir / "_forward_cursor.json", {"w0": 0})

        result = prune_all(project_dir, "30d", data_types=("events",))
        assert result["events"] == []
        assert (events_dir / old).exists()

    def test_multiple_writers_all_must_be_forwarded(self, project_dir: Path) -> None:
        old = (date.today() - timedelta(days=60)).isoformat()
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, old, writer_key="w0", offsets=[0, 1])
        self._wal_segment(events_dir, old, writer_key="w1", offsets=[0])
        # w0 fully forwarded, w1 not mentioned at all in the cursor.
        self._save_cursor(events_dir / "_forward_cursor.json", {"w0": 1})

        result = prune_all(project_dir, "30d", data_types=("events",))
        assert result["events"] == []
        assert (events_dir / old).exists()

        # Now w1 catches up too -> both writers covered -> prunable.
        self._save_cursor(events_dir / "_forward_cursor.json", {"w0": 1, "w1": 0})
        result2 = prune_all(project_dir, "30d", data_types=("events",))
        assert (events_dir / old) in result2["events"]

    def test_recent_unforwarded_date_dir_untouched_regardless(self, project_dir: Path) -> None:
        """A date dir inside the retention window is never pruned anyway —
        the forward-aware guard doesn't change that baseline."""
        recent = date.today().isoformat()
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, recent, writer_key="w0", offsets=[0])

        result = prune_all(project_dir, "30d", data_types=("events",))
        assert result["events"] == []
        assert (events_dir / recent).exists()

    def test_prune_date_dirs_without_skip_unforwarded_ignores_cursor(
        self, project_dir: Path
    ) -> None:
        """Direct `prune_date_dirs` calls (the default `skip_unforwarded=False`)
        behave exactly as before this feature existed — used by non-events
        callers (`channels`/`files`' non-ref-aware fallback, generic
        `data_types` entries) that must never be silently gated by an events
        cursor that has nothing to do with them."""
        from testerkit.data.retention import prune_date_dirs

        old = date.today() - timedelta(days=60)
        events_dir = project_dir / "events"
        self._wal_segment(events_dir, old.isoformat(), writer_key="w0", offsets=[0, 1, 2])
        # No cursor -- would be pinned under skip_unforwarded=True, but the
        # default call here doesn't ask for that.
        removed = prune_date_dirs(events_dir, date.today())
        assert removed == [events_dir / old.isoformat()]
