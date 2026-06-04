"""Catalog aggregator keeps the two libraries SEPARATE, labeled, badged.

Asserts exact bucket names, badges, and that NO merge/dedupe happens.
"""

import service.tests._bootstrap  # noqa: F401

import unittest

from service.core.catalog import Catalog
from service.sources.base.source import CatalogEntry, Source, StreamHandle


class _FakeSource(Source):
    """Minimal in-test source so the catalog test is hermetic."""

    def __init__(self, name, badge, entries):
        self.name = name
        self.badge = badge
        self._entries = entries

    def browse(self, path=None):
        return list(self._entries)

    def open(self, id):  # pragma: no cover - not exercised here
        raise NotImplementedError

    def health(self):
        return {"name": self.name, "reachable": True}


class CatalogSeparationTest(unittest.TestCase):
    def setUp(self):
        # DVD entry deliberately has NO badge set -> catalog should stamp it.
        self.dvd = _FakeSource(
            "DVD Dumps",
            "field-exact",
            [
                CatalogEntry(
                    id="dvddump:matrix-1999",
                    title="The Matrix",
                    kind="title",
                ),
                CatalogEntry(
                    id="dvddump:heat-1995",
                    title="Heat",
                    kind="title",
                ),
            ],
        )
        # Plex entry with a poster + a SAME title as DVD (must NOT dedupe).
        self.plex = _FakeSource(
            "Plex",
            "transcoded",
            [
                CatalogEntry(
                    id="plex:12345",
                    title="The Matrix",  # same title as a DVD entry on purpose
                    kind="movie",
                    poster_url="http://plex/poster/12345",
                )
            ],
        )

    def test_two_separate_labeled_buckets(self):
        cat = Catalog([self.dvd, self.plex])
        result = cat.browse()

        # Exactly two buckets, named exactly by library.
        self.assertEqual(list(result.keys()), ["DVD Dumps", "Plex"])
        self.assertEqual(len(result["DVD Dumps"]), 2)
        self.assertEqual(len(result["Plex"]), 1)

    def test_badges_are_correct_and_stamped(self):
        cat = Catalog([self.dvd, self.plex])
        result = cat.browse()

        self.assertEqual(
            [e.badge for e in result["DVD Dumps"]],
            ["field-exact", "field-exact"],
        )
        self.assertEqual([e.badge for e in result["Plex"]], ["transcoded"])

    def test_same_title_appears_in_both_no_dedupe(self):
        cat = Catalog([self.dvd, self.plex])
        result = cat.browse()

        dvd_titles = [e.title for e in result["DVD Dumps"]]
        plex_titles = [e.title for e in result["Plex"]]
        self.assertIn("The Matrix", dvd_titles)
        self.assertIn("The Matrix", plex_titles)
        # Distinct ids across libraries; no merge.
        self.assertEqual(result["DVD Dumps"][0].id, "dvddump:matrix-1999")
        self.assertEqual(result["Plex"][0].id, "plex:12345")

    def test_registration_order_is_display_order(self):
        cat = Catalog([self.plex, self.dvd])  # Plex first this time
        self.assertEqual(list(cat.browse().keys()), ["Plex", "DVD Dumps"])

    def test_duplicate_library_name_rejected(self):
        cat = Catalog([self.dvd])
        with self.assertRaises(ValueError):
            cat.register(_FakeSource("DVD Dumps", "field-exact", []))

    def test_find_source_and_health(self):
        cat = Catalog([self.dvd, self.plex])
        self.assertIs(cat.find_source("Plex"), self.plex)
        self.assertIsNone(cat.find_source("Nope"))
        health = cat.health()
        self.assertEqual(set(health.keys()), {"DVD Dumps", "Plex"})


if __name__ == "__main__":
    unittest.main()
