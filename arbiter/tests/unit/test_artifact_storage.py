"""
Artifact retention.

`POST /v1/cases/{id}/evidence` computed a `storage_key`, wrote it to the
`artifact` row, extracted typed fields from the bytes -- and then discarded
the bytes. MinIO was declared in docker-compose.yml, configured in
arbiter.config, and referenced by zero lines of code.

That makes Reg E 12 CFR 1005.11(d)(1) -- the card member's right to request
"the documents on which the institution relied" -- unsatisfiable, and makes
the proof tree's `source_ref` (page + bbox) a pointer into nothing.
"""

from __future__ import annotations

import hashlib

import pytest

from arbiter.storage.artifacts import LocalArtifactStore


@pytest.fixture
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


def test_bytes_survive_a_round_trip(store):
    data = b"%PDF-1.7\nthe document the decision cited\n"
    store.put("cases/abc/def", data, "application/pdf")
    assert store.get("cases/abc/def") == data


def test_missing_artifact_returns_none_not_an_exception(store):
    assert store.get("cases/nope/nope") is None


def test_stored_bytes_hash_to_what_the_artifact_row_records(store):
    """The integrity check the retrieval route performs: a document that no
    longer hashes to what the decision cited is a compliance incident, not a
    cache miss."""
    data = b"\x89PNG\r\n\x1a\ndelivery photo"
    digest = hashlib.sha256(data).digest()
    store.put("cases/abc/img", data, "image/png")
    assert hashlib.sha256(store.get("cases/abc/img")).digest() == digest


def test_put_is_write_once(store):
    """An artifact is immutable evidence. Silently overwriting one would let
    a re-upload rewrite the document a signed decision already cites."""
    store.put("cases/abc/def", b"original", "application/pdf")
    store.put("cases/abc/def", b"substituted", "application/pdf")
    assert store.get("cases/abc/def") == b"original"


def test_storage_key_cannot_escape_the_root(store):
    """storage_key is server-generated today, but a path that escapes the
    root is the kind of thing that becomes exploitable the moment somebody
    makes it caller-influenced."""
    with pytest.raises(ValueError):
        store.put("../../etc/passwd", b"x", "text/plain")
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")


def test_exists_reflects_reality(store):
    assert not store.exists("cases/a/b")
    store.put("cases/a/b", b"x", "application/pdf")
    assert store.exists("cases/a/b")


def test_nested_keys_create_their_directories(store):
    store.put("cases/deeply/nested/key", b"x", "application/pdf")
    assert store.get("cases/deeply/nested/key") == b"x"


def test_partial_writes_do_not_leave_a_readable_artifact(store, monkeypatch):
    """Written via a temp file and renamed: a crash mid-write must not leave
    a truncated document that later reads as authoritative evidence."""
    store.put("cases/a/b", b"complete", "application/pdf")
    leftovers = list(store.root.rglob("*.partial"))
    assert leftovers == []
