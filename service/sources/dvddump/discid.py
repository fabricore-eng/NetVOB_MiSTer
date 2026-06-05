"""Disc content fingerprint — a stable, content-derived identity for a DVD dump.

Why this exists (``catalog-browse.md`` §5, "disc-ID lookup"): the catalog needs a
**stable id per disc** so that

  * a title's catalog id survives the folder being renamed/moved,
  * the same disc dumped twice is recognisable as the same disc (re-dump detection),
  * titles can be grouped under their disc in the browse UI.

How: hash the disc's **IFO files**. The ``.IFO`` files (``VIDEO_TS.IFO`` = VMGI, and
each ``VTS_nn_0.IFO`` = VTSI) fully describe the disc's structure — title sets, PGCs,
program/cell/VOBU maps — in a few hundred KB total. Two different discs essentially
never share an identical IFO set, and the IFOs are byte-identical across copies of the
same dump, so hashing them gives a fingerprint that is both **unique** and **stable**.
We deliberately do *not* hash the multi-GB ``.VOB`` payload: it adds cost without adding
discrimination (the IFOs already encode the structure that makes a disc unique).

**NOT the Windows Media Center / pydvdid "DVD ID".** That algorithm hashes the FAT
*creation timestamps* of the VIDEO_TS files, which are (a) not preserved by many dump
tools and (b) differ between two copies of the same disc — so it is unstable for our
purpose and is designed for a different one (online metadata lookup). pydvdid stays an
*optional, future* dependency for that separate lookup path (see ``requirements.txt``);
this fingerprint is our internal catalog identity and makes no compatibility claim.

Stdlib only (``hashlib.blake2b``); no third-party deps.
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import Optional

# Cap per-IFO read so a pathologically large VTSI (big VOBU address map) can't make a
# fingerprint cost unbounded. 16 MiB is far beyond any real IFO (they are < ~1 MiB), so
# in practice the whole file is always hashed; this is purely a safety bound.
_MAX_IFO_BYTES = 16 * 1024 * 1024

# blake2b digest size in bytes -> the fingerprint is this many hex chars * 2.
# 8 bytes (16 hex) matches the conventional 64-bit "disc id" width and is collision-safe
# for any realistic library size.
_DIGEST_BYTES = 8

_ID_PREFIX = "dvddump:disc-"


class NoDvdStructure(FileNotFoundError):
    """Raised when a directory has no ``.IFO`` files (not a DVD-Video dump)."""


def _resolve_video_ts(path: str) -> str:
    """Return the directory that actually holds the ``.IFO`` files.

    Accepts either the ``VIDEO_TS`` directory itself or a disc-root directory that
    *contains* a ``VIDEO_TS`` (case-insensitive) child, so callers can pass whichever
    they have.
    """
    if not os.path.isdir(path):
        raise NoDvdStructure(f"not a directory: {path!r}")
    # Already a VIDEO_TS-style dir (has IFOs)?
    if _list_ifos(path):
        return path
    # Otherwise look for a VIDEO_TS child (any case).
    for entry in os.listdir(path):
        child = os.path.join(path, entry)
        if entry.upper() == "VIDEO_TS" and os.path.isdir(child):
            return child
    return path  # let _list_ifos() below raise with a useful message


def _list_ifos(directory: str) -> list[str]:
    """Return absolute paths of ``*.IFO`` files in ``directory``, case-insensitive.

    Sorted by **upper-cased name** so the order is independent of the filesystem's
    enumeration order and of filename casing — two copies of a disc that differ only in
    case (``vts_01_0.ifo`` vs ``VTS_01_0.IFO``) fingerprint identically.
    """
    try:
        names = os.listdir(directory)
    except (FileNotFoundError, NotADirectoryError):
        return []
    ifos = [n for n in names if n.upper().endswith(".IFO")]
    ifos.sort(key=str.upper)
    return [os.path.join(directory, n) for n in ifos]


def disc_fingerprint(path: str) -> str:
    """Compute the stable content fingerprint for the DVD dump at ``path``.

    ``path`` may be the ``VIDEO_TS`` directory or a disc-root containing it. Returns a
    16-char lowercase hex digest. Raises :class:`NoDvdStructure` if no ``.IFO`` files
    are found (so the caller can fall back to a filename-based id).

    The hashed buffer, per IFO in name order, is::

        UPPERCASE_NAME b"\\0"  +  uint32_be(file_size)  +  file_bytes(<=16 MiB)

    Including the name and size (not just bytes) keeps the boundary between files
    unambiguous, so concatenation can't alias two different layouts to one digest.
    """
    video_ts = _resolve_video_ts(path)
    ifos = _list_ifos(video_ts)
    if not ifos:
        raise NoDvdStructure(f"no .IFO files under {path!r} — not a DVD-Video dump")

    h = hashlib.blake2b(digest_size=_DIGEST_BYTES)
    for ifo_path in ifos:
        name = os.path.basename(ifo_path).upper().encode("ascii", "replace")
        try:
            size = os.path.getsize(ifo_path)
        except OSError:
            # A listed-but-unreadable IFO: fold the name in so the digest still changes,
            # but skip its (unreadable) bytes rather than aborting the whole fingerprint.
            h.update(name + b"\0" + struct.pack(">I", 0))
            continue
        h.update(name + b"\0" + struct.pack(">I", size & 0xFFFFFFFF))
        with open(ifo_path, "rb") as fh:
            remaining = _MAX_IFO_BYTES
            while remaining > 0:
                chunk = fh.read(min(1 << 20, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    return h.hexdigest()


def disc_id(path: str) -> str:
    """Return the namespaced disc id ``"dvddump:disc-<fingerprint>"`` for ``path``.

    Raises :class:`NoDvdStructure` if ``path`` is not a DVD-Video dump.
    """
    return _ID_PREFIX + disc_fingerprint(path)


def try_disc_id(path: str) -> Optional[str]:
    """Like :func:`disc_id` but return ``None`` instead of raising when ``path`` has no
    DVD structure — convenient for ``browse()`` which must not fail on a stray folder."""
    try:
        return disc_id(path)
    except (NoDvdStructure, OSError):
        return None
