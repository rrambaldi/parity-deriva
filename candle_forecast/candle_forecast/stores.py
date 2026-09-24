"""Store HDF5 zippati dentro il repo (`stores/<nome>.zip`), così i dati di training viaggiano con git.

`pack` li scrive da parity_deriva settings.DATA_DIR; `unpack` li scompatta accanto allo zip prima
dell'uso (la copia scompattata non va in git). Lo SHA-256 dello store sta nel commento dello zip:
serve a non riscrivere uno zip uguale (git non vede modifiche) e a verificare lo scompattato.
Compressione LZMA: metà del deflate (15 MB contro 32 per EUR_USD), e ogni aggiornamento resta nella
storia di git. Si apre con Python e 7-Zip; lo `unzip` classico non conosce LZMA."""
from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)       # data fissa nello zip: stesso contenuto, stessi byte


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def zip_sha(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        return z.comment.decode()


def pack(src: Path, zip_path: Path) -> bool:
    """Zippa `src` in `zip_path`. False se lo zip c'era già con lo stesso contenuto."""
    digest = sha256(src)
    if zip_path.exists() and zip_sha(zip_path) == digest:
        return False
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".zip.tmp")
    info = zipfile.ZipInfo(src.name, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_LZMA
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(tmp, "w") as z:
        z.comment = digest.encode()
        with open(src, "rb") as fin, z.open(info, "w") as fout:
            for block in iter(lambda: fin.read(1 << 20), b""):
                fout.write(block)
    os.replace(tmp, zip_path)
    return True


def unpack(zip_path: Path) -> Path:
    """Lo store scompattato accanto allo zip; si riscompatta solo se lo zip è cambiato."""
    digest = zip_sha(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        target = zip_path.parent / name
        mark = target.with_name(name + ".sha256")
        if target.exists() and mark.exists() and mark.read_text().strip() == digest:
            return target
        tmp = target.with_name(name + ".tmp")
        with z.open(name) as fin, open(tmp, "wb") as fout:
            for block in iter(lambda: fin.read(1 << 20), b""):
                fout.write(block)
    if sha256(tmp) != digest:
        tmp.unlink()
        raise RuntimeError(f"{zip_path}: lo store scompattato non corrisponde allo SHA-256 dello zip")
    os.replace(tmp, target)
    mark.write_text(digest + "\n")
    return target
