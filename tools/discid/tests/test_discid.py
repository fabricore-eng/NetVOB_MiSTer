"""Tests for tools.discid -- fingerprint + resolver chain.

All committed fixtures are SYNTHETIC (tiny fake VIDEO_TS dirs). The one test that
touches the real, copyrighted KUNGPOW IFOs reads them from the local-only
``/tmp/kungpow_slice`` and SKIPS cleanly when that path is absent -- no real disc
bytes are ever copied into the repo or into a committed fixture.

Stdlib only: unittest, os, tempfile, shutil, json.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tools.discid import discid as discid_mod
from tools.discid.discid import disc_fingerprint
from tools.discid.metadata import (
    SOURCE_CACHE,
    SOURCE_FALLBACK,
    SOURCE_SIDECAR,
    SOURCE_TMDB,
    cache_put,
    resolve_title,
)

REAL_SLICE = "/tmp/kungpow_slice/VIDEO_TS"


# --------------------------------------------------------------------------- #
# synthetic fixture helpers
# --------------------------------------------------------------------------- #
def _make_ifo(path: str, magic: bytes, size: int, seed: int = 0) -> None:
    """Write a tiny fake IFO: 12-byte magic + deterministic body padded to *size*."""
    assert size >= 16
    body = bytes(((i * 31 + seed * 7) & 0xFF) for i in range(size - len(magic)))
    with open(path, "wb") as fh:
        fh.write(magic)
        fh.write(body)


def _make_video_ts(parent: str, title_sets: int = 2, seed: int = 0) -> str:
    """Create ``<parent>/VIDEO_TS`` with a VMG IFO + N VTS IFOs. Returns its path."""
    video_ts = os.path.join(parent, "VIDEO_TS")
    os.makedirs(video_ts, exist_ok=True)
    _make_ifo(os.path.join(video_ts, "VIDEO_TS.IFO"), b"DVDVIDEO-VMG", 2048, seed)
    for n in range(1, title_sets + 1):
        _make_ifo(
            os.path.join(video_ts, f"VTS_{n:02d}_0.IFO"),
            b"DVDVIDEO-VTS",
            1024 + n * 256,
            seed + n,
        )
        # A .BUP backup and a .VOB payload that MUST be ignored by the fingerprint.
        with open(os.path.join(video_ts, f"VTS_{n:02d}_0.BUP"), "wb") as fh:
            fh.write(b"\x00" * 512)
        with open(os.path.join(video_ts, f"VTS_{n:02d}_1.VOB"), "wb") as fh:
            fh.write(b"\xFF" * 4096)
    return video_ts


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="discid_fp_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_determinism(self) -> None:
        """Same dir -> same fingerprint across repeated calls."""
        vts = _make_video_ts(os.path.join(self.tmp, "MovieA"))
        fp1 = disc_fingerprint(vts)
        fp2 = disc_fingerprint(vts)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)  # sha-256 hex
        int(fp1, 16)  # valid hex

    def test_order_independence(self) -> None:
        """Two byte-identical dumps in different dirs hash the same.

        Directory-listing order can differ between filesystems; the fingerprint
        sorts records by basename, so identical content must produce one digest.
        """
        vts_a = _make_video_ts(os.path.join(self.tmp, "A"), title_sets=3, seed=5)
        vts_b = _make_video_ts(os.path.join(self.tmp, "B"), title_sets=3, seed=5)
        self.assertEqual(disc_fingerprint(vts_a), disc_fingerprint(vts_b))

    def test_order_independence_synthetic_reorder(self) -> None:
        """Force a reversed listdir and confirm the hash is unchanged."""
        vts = _make_video_ts(os.path.join(self.tmp, "Reorder"))
        baseline = disc_fingerprint(vts)
        real_listdir = os.listdir
        try:
            discid_mod.os.listdir = lambda p: list(reversed(real_listdir(p)))
            reordered = disc_fingerprint(vts)
        finally:
            discid_mod.os.listdir = real_listdir
        self.assertEqual(baseline, reordered)

    def test_sensitivity_different_ifo(self) -> None:
        """Different IFO content (different seed) -> different fingerprint."""
        vts_a = _make_video_ts(os.path.join(self.tmp, "A"), title_sets=2, seed=1)
        vts_b = _make_video_ts(os.path.join(self.tmp, "B"), title_sets=2, seed=2)
        self.assertNotEqual(disc_fingerprint(vts_a), disc_fingerprint(vts_b))

    def test_sensitivity_different_size(self) -> None:
        """A changed IFO size -> different fingerprint."""
        vts = _make_video_ts(os.path.join(self.tmp, "Resize"))
        fp_before = disc_fingerprint(vts)
        # Grow VIDEO_TS.IFO by appending bytes (changes size + header tail? no --
        # only size, since header is first 1024 and file was 2048; append still
        # changes getsize and thus the record).
        with open(os.path.join(vts, "VIDEO_TS.IFO"), "ab") as fh:
            fh.write(b"\x00" * 16)
        self.assertNotEqual(fp_before, disc_fingerprint(vts))

    def test_sensitivity_different_titleset_count(self) -> None:
        """Adding a title set -> different fingerprint."""
        vts2 = _make_video_ts(os.path.join(self.tmp, "Two"), title_sets=2, seed=9)
        vts3 = _make_video_ts(os.path.join(self.tmp, "Three"), title_sets=3, seed=9)
        self.assertNotEqual(disc_fingerprint(vts2), disc_fingerprint(vts3))

    def test_vob_payload_ignored(self) -> None:
        """Changing only the .VOB payload must NOT change the fingerprint.

        This is what lets us fingerprint a partial dump (IFOs intact, VOB sliced).
        """
        vts = _make_video_ts(os.path.join(self.tmp, "Sliced"))
        fp_before = disc_fingerprint(vts)
        with open(os.path.join(vts, "VTS_01_1.VOB"), "wb") as fh:
            fh.write(b"\x42" * 1_000_000)  # totally different VOB size + content
        self.assertEqual(fp_before, disc_fingerprint(vts))

    def test_empty_dir_raises(self) -> None:
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        with self.assertRaises(FileNotFoundError):
            disc_fingerprint(empty)


class ResolverChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="discid_res_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Each test gets its own cache file so they never collide with the real one.
        self.cache_path = os.path.join(self.tmp, "cache.json")
        # Dump lives at <tmp>/KUNGPOW/VIDEO_TS so folder name == "KUNGPOW".
        self.movie_dir = os.path.join(self.tmp, "KUNGPOW")
        self.vts = _make_video_ts(self.movie_dir)
        self.fp = disc_fingerprint(self.vts)
        # Ensure no ambient TMDB key leaks into "skip" tests.
        self._saved_key = os.environ.pop("TMDB_API_KEY", None)

    def tearDown(self) -> None:
        if self._saved_key is not None:
            os.environ["TMDB_API_KEY"] = self._saved_key

    def test_folder_fallback(self) -> None:
        """No sidecar, no cache, no key -> folder name, source=fallback."""
        res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "KUNGPOW")
        self.assertEqual(res["source"], SOURCE_FALLBACK)
        self.assertEqual(res["fingerprint"], self.fp)

    def test_cache_beats_folder(self) -> None:
        """A cache entry for the fingerprint wins over the folder name."""
        cache_put(
            self.fp,
            {"title": "Kung Pow! Enter the Fist", "year": 2002, "poster": "p.jpg"},
            cache_path=self.cache_path,
        )
        res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "Kung Pow! Enter the Fist")
        self.assertEqual(res["year"], 2002)
        self.assertEqual(res["source"], SOURCE_CACHE)

    def test_sidecar_beats_cache(self) -> None:
        """A sidecar JSON next to the dump wins over the cache."""
        cache_put(
            self.fp,
            {"title": "From Cache", "year": 1900, "poster": None},
            cache_path=self.cache_path,
        )
        sidecar = os.path.join(self.movie_dir, "KUNGPOW.json")
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump({"title": "Sidecar Title", "year": 2002}, fh)
        res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "Sidecar Title")
        self.assertEqual(res["year"], 2002)
        self.assertEqual(res["source"], SOURCE_SIDECAR)

    def test_sidecar_netvob_json(self) -> None:
        """The fixed 'netvob.json' sidecar name is honored too."""
        with open(os.path.join(self.movie_dir, "netvob.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "Fixed Name Sidecar"}, fh)
        res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "Fixed Name Sidecar")
        self.assertEqual(res["source"], SOURCE_SIDECAR)

    def test_sidecar_nfo(self) -> None:
        """An .nfo sidecar (XML title) is parsed."""
        with open(os.path.join(self.movie_dir, "KUNGPOW.nfo"), "w", encoding="utf-8") as fh:
            fh.write("<movie><title>NFO Title</title><year>2002</year></movie>")
        res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "NFO Title")
        self.assertEqual(res["year"], 2002)
        self.assertEqual(res["source"], SOURCE_SIDECAR)

    def test_folder_title_year_parse(self) -> None:
        """'The Matrix (1999)' folder -> title 'The Matrix', year 1999."""
        movie = os.path.join(self.tmp, "The Matrix (1999)")
        vts = _make_video_ts(movie)
        fp = disc_fingerprint(vts)
        res = resolve_title(vts, fp, cache_path=self.cache_path)
        self.assertEqual(res["title"], "The Matrix")
        self.assertEqual(res["year"], 1999)
        self.assertEqual(res["source"], SOURCE_FALLBACK)

    def test_tmdb_skipped_without_key(self) -> None:
        """TMDB path is skipped when no key -> fallback, and TMDB is never called.

        We monkeypatch the network call to fail loudly if it is ever invoked.
        """
        import tools.discid.metadata as md

        def _boom(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("TMDB lookup must NOT run without a key")

        orig = md._tmdb_lookup
        md._tmdb_lookup = _boom
        try:
            res = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        finally:
            md._tmdb_lookup = orig
        self.assertEqual(res["source"], SOURCE_FALLBACK)
        self.assertEqual(res["title"], "KUNGPOW")

    def test_tmdb_used_when_key_present(self) -> None:
        """With a key + a stubbed lookup, TMDB enrichment wins over raw folder."""
        import tools.discid.metadata as md

        def _stub(title, year, api_key):
            self.assertEqual(api_key, "TESTKEY")
            return {
                "title": "Kung Pow: Enter the Fist",
                "year": 2002,
                "poster": "https://image.tmdb.org/t/p/w500/abc.jpg",
                "source": SOURCE_TMDB,
            }

        orig = md._tmdb_lookup
        md._tmdb_lookup = _stub
        try:
            res = resolve_title(
                self.vts, self.fp, cache_path=self.cache_path, tmdb_api_key="TESTKEY"
            )
        finally:
            md._tmdb_lookup = orig
        self.assertEqual(res["source"], SOURCE_TMDB)
        self.assertEqual(res["title"], "Kung Pow: Enter the Fist")
        # And it should have been written to cache for next time.
        with open(self.cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        self.assertIn(self.fp, cache)

    def test_fallback_is_cached(self) -> None:
        """A folder fallback is persisted so the second call reads source=cache."""
        first = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(first["source"], SOURCE_FALLBACK)
        second = resolve_title(self.vts, self.fp, cache_path=self.cache_path)
        self.assertEqual(second["source"], SOURCE_CACHE)
        self.assertEqual(second["title"], "KUNGPOW")


@unittest.skipUnless(
    os.path.isdir(REAL_SLICE),
    f"real KUNGPOW slice not present at {REAL_SLICE} (local-only, copyrighted)",
)
class RealKungPowTests(unittest.TestCase):
    """Touches the local-only real IFOs; SKIPS cleanly if absent. No bytes copied."""

    def test_real_fingerprint_stable(self) -> None:
        fp1 = disc_fingerprint(REAL_SLICE)
        fp2 = disc_fingerprint(REAL_SLICE)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)
        int(fp1, 16)

    def test_real_folder_fallback_yields_title(self) -> None:
        # Resolve without a key and without polluting the real cache.json.
        tmp = tempfile.mkdtemp(prefix="discid_real_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cache_path = os.path.join(tmp, "cache.json")
        saved = os.environ.pop("TMDB_API_KEY", None)
        try:
            fp = disc_fingerprint(REAL_SLICE)
            res = resolve_title(REAL_SLICE, fp, cache_path=cache_path)
        finally:
            if saved is not None:
                os.environ["TMDB_API_KEY"] = saved
        # Parent folder is "kungpow_slice" in the local fixture.
        self.assertTrue(res["title"])  # always a usable name
        self.assertEqual(res["source"], SOURCE_FALLBACK)
        self.assertEqual(res["fingerprint"], fp)


if __name__ == "__main__":
    unittest.main()
