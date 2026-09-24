"""Store zippati: andata e ritorno identici, zip deterministico, riscrittura solo se cambia."""
import pytest

from candle_forecast import stores


def test_roundtrip_and_idempotent(tmp_path):
    src = tmp_path / "data" / "X.hd5"
    src.parent.mkdir()
    src.write_bytes(bytes(range(256)) * 5000)
    z = tmp_path / "stores" / "X.hd5.zip"
    assert stores.pack(src, z) is True
    first = z.read_bytes()
    assert stores.pack(src, z) is False and z.read_bytes() == first      # invariato: git non vede nulla
    z.unlink()
    assert stores.pack(src, z) is True and z.read_bytes() == first       # stessi byte a ogni pack
    out = stores.unpack(z)
    assert out == z.parent / "X.hd5" and out.read_bytes() == src.read_bytes()
    mtime = out.stat().st_mtime_ns
    assert stores.unpack(z) == out and out.stat().st_mtime_ns == mtime  # già scompattato: non lo rifà


def test_changed_store_is_repacked_and_reextracted(tmp_path):
    src = tmp_path / "X.hd5"
    src.write_bytes(b"a" * 1000)
    z = tmp_path / "stores" / "X.hd5.zip"
    stores.pack(src, z)
    stores.unpack(z)
    src.write_bytes(b"b" * 1000)
    assert stores.pack(src, z) is True
    assert stores.unpack(z).read_bytes() == b"b" * 1000


def test_corrupt_extraction_refused(tmp_path, monkeypatch):
    src = tmp_path / "X.hd5"
    src.write_bytes(b"c" * 1000)
    z = tmp_path / "stores" / "X.hd5.zip"
    stores.pack(src, z)
    monkeypatch.setattr(stores, "zip_sha", lambda p: "0" * 64)          # checksum che non torna
    with pytest.raises(RuntimeError, match="SHA-256"):
        stores.unpack(z)
    assert not (z.parent / "X.hd5").exists()
