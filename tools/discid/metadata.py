"""NetVOB DVD-dump title-metadata resolver.

Given a ``VIDEO_TS`` directory and its disc fingerprint, resolve a human title
(and optionally year / poster) for the dump. VOB/IFO files carry no human title,
so we walk a fallback chain (see ``docs/catalog-browse.md`` Section 5):

    (a) SIDECAR  -- a user-provided ``<name>.json`` or ``<name>.nfo`` next to the
                    dump (i.e. in the directory that *contains* ``VIDEO_TS``).
                    Always wins; needs no network. Lets the user correct a match.
    (b) CACHE    -- ``tools/discid/cache.json`` keyed by fingerprint. A prior
                    resolution (e.g. a TMDB hit) is reused offline. Gitignored.
    (c) FOLDER   -- the parent folder name of ``VIDEO_TS`` (e.g.
                    ``.../KUNGPOW/VIDEO_TS`` -> ``"KUNGPOW"``). Always available,
                    so the disc is never unbrowsable.
    (d) TMDB     -- a TMDB "search by name" lookup to enrich (c)'s guessed title
                    with the real title/year/poster. GATED on the ``TMDB_API_KEY``
                    env var: absent -> skipped, and we return the folder/sidecar
                    result with ``source="fallback"``. Never crashes without creds.

Precedence: sidecar > cache > (folder, optionally enriched by TMDB).

Result dict shape (DRAFT_SCHEMA):
    {
        "title":   str,            # always present
        "year":    int | None,     # optional
        "poster":  str | None,     # optional (URL or path)
        "source":  str,            # which chain rung produced it (see SOURCE_*)
        "fingerprint": str,        # echoed disc fingerprint
    }

Stdlib only: ``json``, ``os``, ``re``, ``urllib`` for the (gated) TMDB call.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

# --- result["source"] tags ---------------------------------------------------
SOURCE_SIDECAR = "sidecar"
SOURCE_CACHE = "cache"
SOURCE_TMDB = "tmdb"
SOURCE_FALLBACK = "fallback"  # folder name only (no network / no TMDB key)

# Cache lives next to this module; gitignored (see .gitignore).
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")

# Sidecar basenames we honor, plus the convention of "<parent-folder-name>.json".
_FIXED_SIDECAR_NAMES = ("netvob.json",)

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_TIMEOUT_S = 8


# --------------------------------------------------------------------------- #
# (c) folder-name heuristic
# --------------------------------------------------------------------------- #
def _parent_dir(video_ts_dir: str) -> str:
    """Directory that *contains* VIDEO_TS (where sidecars live)."""
    return os.path.dirname(os.path.normpath(video_ts_dir))


def _folder_name(video_ts_dir: str) -> str:
    """Parent folder name of VIDEO_TS, e.g. .../KUNGPOW/VIDEO_TS -> 'KUNGPOW'."""
    return os.path.basename(_parent_dir(video_ts_dir))


_YEAR_RE = re.compile(r"\(?\b(19\d{2}|20\d{2})\b\)?")


def parse_folder_title(folder: str) -> tuple[str, int | None]:
    """Turn a folder name into a (title, year?) guess.

    Examples:
        "The Matrix (1999)"  -> ("The Matrix", 1999)
        "KUNGPOW"            -> ("KUNGPOW", None)
        "kung_pow.2002"      -> ("kung pow", 2002)
    """
    name = folder.strip()
    year: int | None = None
    m = _YEAR_RE.search(name)
    if m:
        year = int(m.group(1))
        name = name[: m.start()] + name[m.end() :]
    # Normalize separators to spaces and collapse whitespace.
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -")
    if not name:
        name = folder.strip() or "Unknown Disc"
    return name, year


# --------------------------------------------------------------------------- #
# (a) sidecar
# --------------------------------------------------------------------------- #
def _read_sidecar(video_ts_dir: str) -> dict | None:
    """Honor a user-provided sidecar next to the dump, if present.

    Looks for (in order):
      - ``netvob.json`` in the parent dir,
      - ``<parent-folder-name>.json`` in the parent dir,
      - ``<parent-folder-name>.nfo`` in the parent dir (XML or plain title text).

    JSON sidecars may set ``title`` (required), ``year``, ``poster``/``poster_url``.
    NFO sidecars: we extract the first ``<title>...</title>`` or, failing that,
    the first non-empty text line. Returns a normalized result dict or None.
    """
    parent = _parent_dir(video_ts_dir)
    folder = _folder_name(video_ts_dir)

    json_candidates = list(_FIXED_SIDECAR_NAMES) + [f"{folder}.json"]
    for cand in json_candidates:
        path = os.path.join(parent, cand)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            title = data.get("title")
            if isinstance(title, str) and title.strip():
                return {
                    "title": title.strip(),
                    "year": data.get("year"),
                    "poster": data.get("poster") or data.get("poster_url"),
                    "source": SOURCE_SIDECAR,
                }

    nfo_path = os.path.join(parent, f"{folder}.nfo")
    if os.path.isfile(nfo_path):
        try:
            with open(nfo_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
        title = None
        m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if m and m.group(1).strip():
            title = m.group(1).strip()
        else:
            for line in text.splitlines():
                if line.strip():
                    title = line.strip()
                    break
        if title:
            ym = re.search(r"<year>(\d{4})</year>", text, re.IGNORECASE)
            year = int(ym.group(1)) if ym else None
            return {"title": title, "year": year, "poster": None, "source": SOURCE_SIDECAR}

    return None


# --------------------------------------------------------------------------- #
# (b) cache (gitignored)
# --------------------------------------------------------------------------- #
def _load_cache(cache_path: str) -> dict:
    if not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_get(fingerprint: str, cache_path: str) -> dict | None:
    entry = _load_cache(cache_path).get(fingerprint)
    if isinstance(entry, dict) and isinstance(entry.get("title"), str):
        out = {
            "title": entry["title"],
            "year": entry.get("year"),
            "poster": entry.get("poster"),
            "source": SOURCE_CACHE,
        }
        return out
    return None


def cache_put(fingerprint: str, result: dict, cache_path: str = CACHE_PATH) -> None:
    """Persist a resolved result under *fingerprint* (best-effort, never raises)."""
    try:
        cache = _load_cache(cache_path)
        cache[fingerprint] = {
            "title": result.get("title"),
            "year": result.get("year"),
            "poster": result.get("poster"),
            # remember which rung originally produced it, for debugging
            "resolved_via": result.get("source"),
        }
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
        os.replace(tmp, cache_path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# (d) TMDB by name (gated on TMDB_API_KEY)
# --------------------------------------------------------------------------- #
def _tmdb_lookup(title: str, year: int | None, api_key: str) -> dict | None:
    """Search TMDB by title; return enriched dict or None. Never raises."""
    params = {"api_key": api_key, "query": title}
    if year:
        params["year"] = str(year)
    url = TMDB_SEARCH_URL + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TMDB_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - network/JSON/anything: degrade gracefully
        return None

    results = payload.get("results") or []
    if not results:
        return None
    top = results[0]
    rel = top.get("release_date") or ""
    got_year: int | None = None
    if len(rel) >= 4 and rel[:4].isdigit():
        got_year = int(rel[:4])
    poster_path = top.get("poster_path")
    poster = TMDB_IMAGE_BASE + poster_path if poster_path else None
    return {
        "title": top.get("title") or title,
        "year": got_year or year,
        "poster": poster,
        "source": SOURCE_TMDB,
    }


# --------------------------------------------------------------------------- #
# public resolver
# --------------------------------------------------------------------------- #
def resolve_title(
    video_ts_dir: str,
    fingerprint: str,
    *,
    cache_path: str = CACHE_PATH,
    tmdb_api_key: str | None = None,
    write_cache: bool = True,
) -> dict:
    """Resolve a title for the dump via sidecar > cache > folder(+TMDB).

    Args:
        video_ts_dir: path to the VIDEO_TS directory.
        fingerprint: the disc fingerprint (cache key) from ``disc_fingerprint``.
        cache_path: override the cache location (tests pass a temp path).
        tmdb_api_key: explicit key; defaults to ``os.environ['TMDB_API_KEY']``.
        write_cache: if True, persist newly resolved (folder/TMDB) results.

    Returns:
        Result dict (see module docstring). ``source`` reflects the winning rung.
        Always returns a usable title; never crashes when TMDB creds are absent.
    """
    # (a) sidecar -- always wins.
    sidecar = _read_sidecar(video_ts_dir)
    if sidecar is not None:
        sidecar["fingerprint"] = fingerprint
        return sidecar

    # (b) cache -- keyed by fingerprint.
    cached = _cache_get(fingerprint, cache_path)
    if cached is not None:
        cached["fingerprint"] = fingerprint
        return cached

    # (c) folder-name heuristic -- the always-available baseline.
    folder = _folder_name(video_ts_dir)
    guess_title, guess_year = parse_folder_title(folder)
    result = {
        "title": guess_title,
        "year": guess_year,
        "poster": None,
        "source": SOURCE_FALLBACK,
        "fingerprint": fingerprint,
    }

    # (d) TMDB enrichment -- GATED on the key. Absent -> keep the fallback.
    if tmdb_api_key is None:
        tmdb_api_key = os.environ.get("TMDB_API_KEY")
    if tmdb_api_key:
        enriched = _tmdb_lookup(guess_title, guess_year, tmdb_api_key)
        if enriched is not None:
            enriched["fingerprint"] = fingerprint
            result = enriched

    # Persist folder/TMDB results so future lookups are offline + correctable.
    if write_cache and result["source"] in (SOURCE_FALLBACK, SOURCE_TMDB):
        cache_put(fingerprint, result, cache_path)

    return result
