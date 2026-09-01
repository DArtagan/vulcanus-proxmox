"""Tests for the manifest generator.

Run with `python3 -m unittest discover tools/mb-seed`.

`manifest.py` runs inside the cluster against mutagen, which is not installed
here — it is a beets dependency that lives in the beets-flask image. The tag
reading is the risky part of this tool and the part worth testing, so mutagen is
stubbed rather than skipped: the stub returns the same shapes the real library
does (tag values are lists, freeform values are bytes).
"""

import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FREEFORM = "----:com.pilabor.tone:"


class FakeInfo:
    def __init__(self, length):
        self.length = length


class FakeCover(bytes):
    """Stands in for mutagen's MP4Cover, which is a bytes subclass carrying an
    image_format code (13 = JPEG, 14 = PNG)."""

    def __new__(cls, data, image_format=13):
        self = super().__new__(cls, data)
        self.imageformat = image_format
        return self


class FakeAudio:
    def __init__(self, tags, length=3600.0):
        self.tags = tags
        self.info = FakeInfo(length) if length is not None else None


def install_mutagen_stub():
    """Put a stub `mutagen` in sys.modules so manifest.py imports cleanly."""
    stub = types.ModuleType("mutagen")
    stub.File = lambda path: FakeAudio.registry.get(path)
    FakeAudio.registry = {}
    sys.modules["mutagen"] = stub
    return stub


install_mutagen_stub()
sys.path.insert(0, HERE)
import manifest  # noqa: E402


def tags(**overrides):
    """A well-formed Libation/tone tag set, as measured off a real rip."""
    base = {
        "\xa9alb": ["Death Cultivator (Unabridged)"],
        "aART": ["Eden Hudson"],
        "\xa9nrt": ["Travis Baldree"],
        "\xa9day": ["2020"],
        "rldt": ["11-Oct-2020"],
        "asin": ["B08M3S7ZPF"],
        FREEFORM + "AUDIBLE_ASIN": [b"B08M3S7ZPF"],
        FREEFORM + "SERIES": [b"Death Cultivator"],
        FREEFORM + "PART": [b"1"],
        FREEFORM + "PUBLISHER": [b"Shadow Alley Press Inc"],
        FREEFORM + "LANGUAGE": [b"English"],
        "covr": [FakeCover(b"\xff\xd8\xff\xe0")],
    }
    for key, value in overrides.items():
        real = {
            "album": "\xa9alb",
            "author": "aART",
            "artist": "\xa9ART",
            "narrator": "\xa9nrt",
            "day": "\xa9day",
            "released": "rldt",
            "plain_asin": "asin",
            "asin": FREEFORM + "AUDIBLE_ASIN",
            "series": FREEFORM + "SERIES",
            "part": FREEFORM + "PART",
            "publisher": FREEFORM + "PUBLISHER",
            "language": FREEFORM + "LANGUAGE",
            "covr": "covr",
        }[key]
        if value is None:
            base.pop(real, None)
        else:
            base[real] = value
    return base


def record(root="/audio/import", path="/audio/import/Book/book.m4b", sidecars=None,
           **overrides):
    FakeAudio.registry[path] = FakeAudio(tags(**overrides))
    listing = ["book.m4b"] + (["cover.jpg"] if sidecars is None else list(sidecars))
    original = manifest.os.listdir
    manifest.os.listdir = lambda d: listing
    try:
        return manifest.record(path, root)
    finally:
        manifest.os.listdir = original


class TestParseDate(unittest.TestCase):
    def test_prefers_rldt_which_carries_a_full_date(self):
        self.assertEqual(
            manifest.parse_date(tags()), {"year": 2020, "month": 10, "day": 11}
        )

    def test_falls_back_to_year_only_day_tag(self):
        self.assertEqual(manifest.parse_date(tags(released=None)), {"year": 2020})

    def test_parses_an_iso_day_tag(self):
        parsed = manifest.parse_date(tags(released=None, day=["2019-03-07"]))
        self.assertEqual(parsed, {"year": 2019, "month": 3, "day": 7})

    def test_returns_none_when_undated(self):
        self.assertIsNone(manifest.parse_date(tags(released=None, day=None)))

    def test_malformed_rldt_falls_back_rather_than_raising(self):
        parsed = manifest.parse_date(tags(released=["sometime in 2020"]))
        self.assertEqual(parsed, {"year": 2020})

    def test_unknown_month_name_falls_back(self):
        parsed = manifest.parse_date(tags(released=["11-Smarch-2020"]))
        self.assertEqual(parsed, {"year": 2020})


class TestTitleCleanup(unittest.TestCase):
    def test_strips_the_unabridged_suffix(self):
        self.assertEqual(record()["title"], "Death Cultivator")

    def test_strips_case_insensitively(self):
        self.assertEqual(record(album=["Kraken (unabridged)"])["title"], "Kraken")

    def test_strips_abridged_too(self):
        self.assertEqual(record(album=["Kraken (Abridged)"])["title"], "Kraken")

    def test_keeps_the_raw_album_for_reference(self):
        self.assertEqual(record()["raw_album"], "Death Cultivator (Unabridged)")

    def test_leaves_a_parenthetical_that_is_part_of_the_title(self):
        title = record(album=["We Are Legion (We Are Bob)"])["title"]
        self.assertEqual(title, "We Are Legion (We Are Bob)")


class TestFieldMapping(unittest.TestCase):
    def test_maps_the_tone_freeform_atoms(self):
        book = record()
        self.assertEqual(book["asin"], "B08M3S7ZPF")
        self.assertEqual(book["series"], "Death Cultivator")
        self.assertEqual(book["part"], "1")
        self.assertEqual(book["publisher"], "Shadow Alley Press Inc")

    def test_maps_language_to_iso_639_3(self):
        self.assertEqual(record()["language"], "eng")

    def test_leaves_an_unmapped_language_unset_rather_than_guessing(self):
        book = record(language=[b"Klingon"])
        self.assertIsNone(book["language"])
        self.assertIn("unmapped language 'Klingon'", book["warnings"])

    def test_prefers_albumartist_over_artist_for_the_author(self):
        self.assertEqual(record(artist=["Somebody Else"])["author"], "Eden Hudson")

    def test_falls_back_to_artist_when_albumartist_is_absent(self):
        book = record(author=None, artist=["Eden Hudson"])
        self.assertEqual(book["author"], "Eden Hudson")

    def test_falls_back_to_the_plain_asin_atom(self):
        self.assertEqual(record(asin=None)["asin"], "B08M3S7ZPF")

    def test_an_audible_asin_records_its_provenance(self):
        self.assertEqual(record()["asin_source"], "audible")

    def test_a_plain_asin_alone_has_unknown_provenance(self):
        # The plain `asin` atom is where an Amazon-sourced file would put its
        # identifier, and Amazon's ASIN is a different namespace from Audible's.
        # Nothing here can tell them apart, so it must not be assumed.
        book = record(asin=None)
        self.assertEqual(book["asin_source"], "unknown")
        self.assertIn("ASIN of unknown provenance", book["warnings"])

    def test_an_audible_asin_is_not_flagged(self):
        self.assertNotIn("ASIN of unknown provenance", record()["warnings"])

    def test_no_asin_has_no_source(self):
        self.assertIsNone(record(asin=None, plain_asin=None)["asin_source"])

    def test_length_is_milliseconds(self):
        self.assertEqual(record()["length_ms"], 3600000)

    def test_paths_are_relative_to_the_root(self):
        book = record(path="/audio/import/Cradle/Unsouled/unsouled.m4b")
        self.assertEqual(book["path"], "Cradle/Unsouled/unsouled.m4b")
        self.assertEqual(book["folder"], "Cradle/Unsouled")

    def test_freeform_values_are_decoded_and_stripped(self):
        self.assertEqual(record(series=[b"  Cradle  "])["series"], "Cradle")

    def test_empty_freeform_reads_as_absent(self):
        self.assertIsNone(record(series=[b""])["series"])


class TestCoverArt(unittest.TestCase):
    """Cover art is embedded in 463 of 485 files and absent from the Cover Art
    Archive for almost all of these releases, so the file is the only source.
    The manifest records where it is; the bytes are fetched on demand, because
    463 covers at a median 553 KiB would be 250 MB of manifest."""

    def test_reports_embedded_art(self):
        art = record()["art"]
        self.assertTrue(art["embedded"])
        self.assertEqual(art["mime"], "image/jpeg")
        self.assertEqual(art["bytes"], 4)

    def test_reports_png_art(self):
        FakeAudio.registry["/audio/import/P/p.m4b"] = FakeAudio(
            tags(covr=[FakeCover(b"\x89PNG", image_format=14)])
        )
        art = manifest.record("/audio/import/P/p.m4b", "/audio/import")["art"]
        self.assertEqual(art["mime"], "image/png")

    def test_reports_no_embedded_art(self):
        art = record(covr=None)["art"]
        self.assertFalse(art["embedded"])
        self.assertIsNone(art["mime"])

    def test_does_not_carry_the_image_bytes(self):
        # The manifest is copied out of the cluster over kubectl exec, which
        # truncates silently; keeping it small is what keeps it whole.
        self.assertNotIn("data", record()["art"])

    def test_lists_sidecar_images(self):
        book = record(sidecars=["cover.jpg", "notes.txt", "back.PNG"])
        self.assertEqual(book["art"]["sidecars"], ["back.PNG", "cover.jpg"])

    def test_no_sidecars_is_an_empty_list(self):
        self.assertEqual(record(sidecars=[])["art"]["sidecars"], [])

    def test_flags_a_book_with_no_art_anywhere(self):
        book = record(covr=None, sidecars=[])
        self.assertIn("no cover art", book["warnings"])

    def test_embedded_art_alone_is_not_flagged(self):
        self.assertNotIn("no cover art", record(sidecars=[])["warnings"])


class TestWarnings(unittest.TestCase):
    def test_a_clean_file_warns_about_nothing(self):
        self.assertEqual(record()["warnings"], [])

    def test_flags_a_missing_author(self):
        book = record(author=None, artist=None)
        self.assertIn("no author credit", book["warnings"])

    def test_flags_a_credit_naming_a_translator(self):
        book = record(author=["Roy, Mana Z - translator"])
        self.assertIn(
            "author credit names someone other than the author", book["warnings"]
        )

    def test_flags_a_credit_naming_an_illustrator(self):
        book = record(author=["Ryohgo Narita, Katsumi Enami - illustrator"])
        self.assertIn(
            "author credit names someone other than the author", book["warnings"]
        )

    def test_flags_a_slash_separated_credit(self):
        book = record(author=["Brandon Sanderson/Michael Kramer"])
        self.assertIn(
            "author credit names someone other than the author", book["warnings"]
        )

    def test_flags_a_missing_asin(self):
        self.assertIn("no ASIN", record(asin=None, plain_asin=None)["warnings"])

    def test_flags_a_missing_narrator(self):
        self.assertIn("no narrator", record(narrator=None)["warnings"])

    def test_unreadable_tags_produce_a_record_rather_than_an_exception(self):
        path = "/audio/import/Broken/broken.m4b"
        FakeAudio.registry[path] = FakeAudio(tags={})
        book = manifest.record(path, "/audio/import")
        self.assertEqual(book["warnings"], ["unreadable tags"])
        self.assertEqual(book["path"], "Broken/broken.m4b")


class TestManifestIsSerialisable(unittest.TestCase):
    def test_a_record_round_trips_through_json(self):
        book = record()
        self.assertEqual(json.loads(json.dumps(book, ensure_ascii=False)), book)

    def test_non_ascii_names_survive(self):
        book = record(author=["추공"])
        self.assertEqual(json.loads(json.dumps(book, ensure_ascii=False))["author"], "추공")


if __name__ == "__main__":
    unittest.main()
