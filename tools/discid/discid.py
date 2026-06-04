"""NetVOB DVD-dump disc fingerprint.

Computes a STABLE, reproducible fingerprint for a ``VIDEO_TS`` directory so the
DVDDumpSource plugin can use it as an internal cache key for resolved title
metadata (see ``docs/catalog-browse.md`` Section 5).

------------------------------------------------------------------------------
Algorithm (NetVOB-discid v1)
------------------------------------------------------------------------------
A DVD-Video disc's ``VIDEO_TS`` directory contains:

  - exactly one ``VIDEO_TS.IFO`` (the Video Manager / VMG nav file), and
  - one ``VTS_nn_0.IFO`` per title set (the Video Title Set nav files).

These ``.IFO`` files are *navigation metadata* (program-chain layout, title
durations, menu structure). They are invariant for a given pressed disc and are
small, so they make a good fingerprint basis -- and crucially they let us
fingerprint a dump even when the large ``.VOB`` payload is sliced/absent (as in
our local KUNGPOW test slice, which keeps the IFOs but only one partial VOB).

For each ``.IFO`` file (the top-level ``VIDEO_TS.IFO`` plus every
``VTS_*_0.IFO``) we build a per-file record from three invariants:

  1. the lowercased basename (e.g. ``"vts_01_0.ifo"``),
  2. the file SIZE in bytes (8-byte big-endian), and
  3. the leading HEADER bytes of the file (default 1024 bytes, or the whole
     file if smaller). The IFO header carries the "DVDVIDEO-VMG" / "DVDVIDEO-VTS"
     magic and the sector-pointer table -- structure that differs between discs
     but is identical for re-dumps of the same disc.

We DELIBERATELY do not hash the full IFO body or any ``.VOB`` payload: bodies can
be padded/zeroed by rippers, and VOBs may be partial. Header + size + name is
enough to discriminate discs while staying robust to partial dumps.

Records are SORTED by basename before hashing, so the result is independent of
directory-listing order (order-independence). Each record is length-prefixed and
fed into a single SHA-256; we return the lowercase hex digest. SHA-256 is in the
stdlib (``hashlib``) -- no third-party dependency.

NOTE ON THE LEGACY ``dvdid`` SPEC: the well-known Windows-Media-Center / XBMC-era
"DVD ID" (libdvdid: a CRC-64 over specific VIDEO_TS.IFO + VTS_01_0.IFO header
fields and file-creation timestamps) is INTENTIONALLY NOT reproduced here.
Matching it byte-for-byte is OPTIONAL because the online dvdid -> title databases
it fed are defunct. Our fingerprint is an *internal* stable cache key, not a
key into any external DB, so a clean documented SHA-256 scheme is preferable to
emulating a legacy CRC. (If we ever want online-DB interop we can add a separate
``libdvdid``-compatible function alongside this one.)

Stability contract:
  - Same VIDEO_TS dir -> same fingerprint, across runs and machines.
  - Reordering / re-listing the same files -> same fingerprint.
  - Any change to an IFO's name, size, or header bytes -> different fingerprint.
"""

from __future__ import annotations

import hashlib
import os
import struct

# Version tag mixed into the hash so a future algorithm change yields a new
# namespace of fingerprints (old cache entries simply miss and get re-resolved).
FINGERPRINT_VERSION = b"netvob-discid-v1"

# How many leading bytes of each IFO to fold into the hash. The DVD spec puts the
# magic + sector pointer table well within the first sector(s); 1 KiB is ample.
HEADER_BYTES = 1024


def _iter_ifo_paths(video_ts_dir: str) -> list[str]:
    """Return absolute paths of the IFO files that define the disc structure.

    Includes ``VIDEO_TS.IFO`` and every ``VTS_*_0.IFO`` (the per-title-set nav
    files). Excludes ``VTS_*_0.BUP`` backups and the ``.VOB`` payload. Matching is
    case-insensitive so it works on dumps from case-preserving and upper-case
    (ISO-9660) filesystems alike.
    """
    paths: list[str] = []
    try:
        names = os.listdir(video_ts_dir)
    except OSError as exc:
        raise FileNotFoundError(
            f"VIDEO_TS directory not readable: {video_ts_dir}"
        ) from exc

    for name in names:
        upper = name.upper()
        if not upper.endswith(".IFO"):
            continue
        # VIDEO_TS.IFO (the VMG) or a VTS_nn_0.IFO title-set nav file.
        if upper == "VIDEO_TS.IFO" or (upper.startswith("VTS_") and upper.endswith("_0.IFO")):
            full = os.path.join(video_ts_dir, name)
            if os.path.isfile(full):
                paths.append(full)
    return paths


def _file_record(path: str) -> bytes:
    """Build the deterministic per-file record (name + size + header bytes)."""
    basename = os.path.basename(path).lower().encode("utf-8")
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        header = fh.read(HEADER_BYTES)

    # Length-prefix every variable-length component so distinct concatenations
    # can never collide (e.g. name "ab"+size vs "a"+"b"+size).
    parts = [
        struct.pack(">Q", len(basename)),
        basename,
        struct.pack(">Q", size),
        struct.pack(">Q", len(header)),
        header,
    ]
    return b"".join(parts)


def disc_fingerprint(video_ts_dir: str) -> str:
    """Compute the stable disc fingerprint for *video_ts_dir*.

    Args:
        video_ts_dir: path to a ``VIDEO_TS`` directory containing ``.IFO`` files.

    Returns:
        Lowercase hex SHA-256 digest (64 chars).

    Raises:
        FileNotFoundError: if the directory is unreadable or has no IFO files.
    """
    ifo_paths = _iter_ifo_paths(video_ts_dir)
    if not ifo_paths:
        raise FileNotFoundError(
            f"no DVD IFO files (VIDEO_TS.IFO / VTS_*_0.IFO) found in: {video_ts_dir}"
        )

    # Order-independence: sort records by basename before hashing.
    records = sorted(_file_record(p) for p in ifo_paths)

    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_VERSION)
    hasher.update(struct.pack(">Q", len(records)))
    for rec in records:
        hasher.update(struct.pack(">Q", len(rec)))
        hasher.update(rec)
    return hasher.hexdigest()


if __name__ == "__main__":  # pragma: no cover - convenience only
    import sys

    if len(sys.argv) != 2:
        print("usage: python3 discid.py <VIDEO_TS dir>", file=sys.stderr)
        raise SystemExit(2)
    print(disc_fingerprint(sys.argv[1]))
