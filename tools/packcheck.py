"""
Brain pack safety checks. Standalone (stdlib + numpy): used by tm_ai's import AND by the hub repository's GitHub
Action (hub_template/tools/packcheck.py is a byte-identical copy; tests/test_hub.py checks that).

A pack is DATA ONLY: a zip of JSON, plain text (TICK input files), and numpy arrays (.npy / .npz without pickled
objects). Nothing in a pack is ever executed, unpickled or imported.
"""
import hashlib
import io
import json
import re
import stat
import zipfile

PACK_FORMAT = 1
ALLOWED_EXT = (".json", ".txt", ".npz", ".npy", ".md")
MAX_ZIP_BYTES = 200 * 1024 * 1024          # the zip file
MAX_MEMBER_BYTES = 100 * 1024 * 1024       # one file, uncompressed
MAX_TOTAL_BYTES = 300 * 1024 * 1024        # all files, uncompressed
MAX_MEMBERS = 500
MAX_RATIO = 200                            # uncompressed / compressed (zip bombs)
_NAME = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\-.]*(/[A-Za-z0-9_\-][A-Za-z0-9_\-.]*)*$")   # no leading dots
_BAD_MAGIC = (b"\x80\x02", b"\x80\x03", b"\x80\x04", b"\x80\x05",   # pickle protocols 2-5
              b"MZ", b"\x7fELF", b"#!", b"PK\x03\x04", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")


class PackError(Exception):
    pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj):
    """Stable JSON text (sorted keys, no spaces): what digests are computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj, field="digest"):
    """sha256 of the canonical JSON of obj without its own digest field."""
    o = {k: v for k, v in obj.items() if k != field}
    return sha256_bytes(canonical(o).encode("utf-8"))


def _check_npy(data, name):
    """A .npy must be a plain numeric array: no pickled objects (dtype 'O')."""
    import numpy as np
    try:
        arr = np.load(io.BytesIO(data), allow_pickle=False)
    except Exception as e:
        raise PackError(f"{name}: not a plain numpy array ({type(e).__name__}: {str(e)[:80]})")
    if arr.dtype.hasobject:
        raise PackError(f"{name}: object arrays are not allowed")


def _check_npz(data, name):
    try:
        inner = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackError(f"{name}: not a valid .npz")
    for m in inner.infolist():
        if not m.filename.endswith(".npy") or "/" in m.filename or "\\" in m.filename or ".." in m.filename:
            raise PackError(f"{name}: unexpected member {m.filename!r}")
        if m.file_size > MAX_MEMBER_BYTES:
            raise PackError(f"{name}/{m.filename}: too large")
        _check_npy(inner.read(m), f"{name}/{m.filename}")


def check_zip(path, expect_sha256=None, expect_size=None):
    """Validate a pack zip. Returns (manifest dict, {member name: bytes}). Raises PackError with a plain reason."""
    import os
    size = os.path.getsize(path)
    if size > MAX_ZIP_BYTES:
        raise PackError(f"zip is {size} bytes, more than {MAX_ZIP_BYTES}")
    if expect_size is not None and int(expect_size) != size:
        raise PackError(f"size mismatch: index says {expect_size}, file is {size}")
    if expect_sha256 is not None and sha256_file(path).lower() != str(expect_sha256).lower():
        raise PackError("sha256 mismatch: the file is not the one the index describes")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise PackError("not a zip file")
    infos = z.infolist()
    if len(infos) > MAX_MEMBERS:
        raise PackError(f"{len(infos)} files, more than {MAX_MEMBERS}")
    total, files, seen = 0, {}, set()
    for m in infos:
        n = m.filename
        if m.is_dir():
            continue
        if n.startswith("/") or "\\" in n or ".." in n.split("/") or ":" in n or not _NAME.match(n):
            raise PackError(f"unsafe file name {n!r}")
        if n.lower() in seen:
            raise PackError(f"duplicate file name {n!r}")
        seen.add(n.lower())
        mode = (m.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise PackError(f"{n}: symbolic links are not allowed")
        if not n.lower().endswith(ALLOWED_EXT):
            raise PackError(f"{n}: file type not allowed (allowed: {', '.join(ALLOWED_EXT)})")
        if m.flag_bits & 0x1:
            raise PackError(f"{n}: encrypted files are not allowed")
        if m.file_size > MAX_MEMBER_BYTES:
            raise PackError(f"{n}: {m.file_size} bytes, too large")
        if m.compress_size and m.file_size / max(m.compress_size, 1) > MAX_RATIO:
            raise PackError(f"{n}: suspicious compression ratio (zip bomb?)")
        total += m.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackError("pack too large when unpacked")
        data = z.read(m)
        if len(data) != m.file_size:
            raise PackError(f"{n}: size does not match its header")
        low = n.lower()
        if not low.endswith((".npy", ".npz")) and data[:4].startswith(_BAD_MAGIC):
            raise PackError(f"{n}: looks like a pickle / executable / archive, not {low.rsplit('.', 1)[-1]}")
        if low.endswith(".npy"):
            _check_npy(data, n)
        elif low.endswith(".npz"):
            _check_npz(data, n)
        elif low.endswith(".json"):
            try:
                json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise PackError(f"{n}: not valid JSON")
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise PackError(f"{n}: not UTF-8 text")
        files[n] = data
    if "manifest.json" not in files:
        raise PackError("manifest.json missing")
    man = json.loads(files["manifest.json"].decode("utf-8"))
    if man.get("pack_format") != PACK_FORMAT:
        raise PackError(f"pack_format {man.get('pack_format')!r} not supported (expected {PACK_FORMAT})")
    listed = man.get("files") or {}
    for n, data in files.items():
        if n == "manifest.json":
            continue
        if n not in listed:
            raise PackError(f"{n}: not listed in the manifest")
        if listed[n] != sha256_bytes(data):
            raise PackError(f"{n}: content does not match the manifest's sha256")
    for n in listed:
        if n not in files:
            raise PackError(f"{n}: listed in the manifest but missing")
    for need in ("model.npz", "model.json"):
        if need not in files:
            raise PackError(f"{need} missing")
    return man, files
