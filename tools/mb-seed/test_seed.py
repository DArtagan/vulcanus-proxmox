"""Tests for the MusicBrainz release seeder.

Run with `python3 -m unittest discover tools/mb-seed`.

The form these tests assert on is submitted to a public database, so the cost of
a silent mistake is an edit someone else has to clean up. The invariants worth
guarding are the ones that are wrong in a way nobody notices: an artist credit
seeded as one joined string still *looks* right in the editor, and only shows up
later as every audiobook filed under "Author read by Narrator".
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seed  # noqa: E402


def book(**overrides):
    base = {
        "path": "Death Cultivator/Death Cultivator [B08M3S7ZPF].m4b",
        "folder": "Death Cultivator",
        "asin": "B08M3S7ZPF",
        "asin_source": "audible",
        "title": "Death Cultivator",
        "raw_album": "Death Cultivator (Unabridged)",
        "author": "Eden Hudson",
        "narrator": "Travis Baldree",
        "series": "Death Cultivator",
        "part": "1",
        "publisher": "Shadow Alley Press Inc",
        "language": "eng",
        "date": {"year": 2020, "month": 10, "day": 11},
        "length_ms": 33760467,
        "art": {"embedded": True, "mime": "image/jpeg", "bytes": 62467, "sidecars": []},
        "warnings": [],
    }
    base.update(overrides)
    return base


def fields(**overrides):
    return dict_of(seed.seed_fields(book(**overrides)))


def dict_of(pairs):
    """Collapse the field list, keeping repeats (`type`) as a list."""
    out = {}
    for key, value in pairs:
        if key in out:
            out[key] = (out[key] if isinstance(out[key], list) else [out[key]]) + [
                value
            ]
        else:
            out[key] = value
    return out


class TestArtistCredit(unittest.TestCase):
    """The credit follows the convention already established across this
    library's releases in MusicBrainz — "author narrated by A, B & C" — and is
    seeded as separate indexed names.

    MusicBrainz stores the credit as an ordered list, and the `$author` inline
    field in the beets config reads `albumartists[0]` from it. Seeding one
    joined name would file every audiobook under author-plus-narrators.
    """

    def test_a_single_narrator_is_a_separate_indexed_name(self):
        seeded = fields()
        self.assertEqual(seeded["artist_credit.names.0.artist.name"], "Eden Hudson")
        self.assertEqual(seeded["artist_credit.names.0.join_phrase"], " narrated by ")
        self.assertEqual(seeded["artist_credit.names.1.artist.name"], "Travis Baldree")

    def test_no_seeded_name_contains_a_join_phrase(self):
        for key, value in seed.seed_fields(book(narrator="A, B, C")):
            if key.endswith(".artist.name"):
                self.assertNotIn("narrated by", value)
                self.assertNotIn("&", value)

    def test_the_author_is_always_first(self):
        credit = seed.artist_credit(book())
        self.assertEqual(credit[0]["artist.name"], "Eden Hudson")

    def test_the_last_narrator_carries_no_join_phrase(self):
        credit = seed.artist_credit(book(narrator="Jeff Hays, Annie Ellicott"))
        self.assertNotIn("join_phrase", credit[-1])

    def test_two_narrators_are_joined_with_an_ampersand(self):
        credit = seed.artist_credit(book(narrator="Jeff Hays, Annie Ellicott"))
        self.assertEqual(
            [n["artist.name"] for n in credit],
            ["Eden Hudson", "Jeff Hays", "Annie Ellicott"],
        )
        self.assertEqual(credit[0]["join_phrase"], " narrated by ")
        self.assertEqual(credit[1]["join_phrase"], " & ")

    def test_three_narrators_use_commas_then_an_ampersand(self):
        # Matches "Teresa: Everybody Loves Large Chests (Vol.5)":
        # Neven Iliev narrated by Jeff Hays, Annie Ellicott & Justin Thomas James
        credit = seed.artist_credit(
            book(
                author="Neven Iliev",
                narrator="Jeff Hays, Annie Ellicott, Justin Thomas James",
            )
        )
        self.assertEqual(
            seed.render_credit(credit),
            "Neven Iliev narrated by Jeff Hays, Annie Ellicott & Justin Thomas James",
        )

    def test_a_full_cast_credits_every_narrator(self):
        credit = seed.artist_credit(
            book(author="Neven Iliev", narrator="A, B, C, D, E, F, G, H, I")
        )
        self.assertEqual(
            [n["artist.name"] for n in credit],
            ["Neven Iliev", "A", "B", "C", "D", "E", "F", "G", "H", "I"],
        )
        self.assertEqual(
            seed.render_credit(credit),
            "Neven Iliev narrated by A, B, C, D, E, F, G, H & I",
        )

    def test_no_narrator_credits_the_author_alone(self):
        credit = seed.artist_credit(book(narrator=None))
        self.assertEqual([n["artist.name"] for n in credit], ["Eden Hudson"])
        self.assertNotIn("join_phrase", credit[0])

    def test_no_author_seeds_no_credit_at_all(self):
        self.assertEqual(seed.artist_credit(book(author=None)), [])

    def test_credit_indices_are_consecutive_from_zero(self):
        # MusicBrainz requires it: "Values for _x_ must be consecutive integers
        # starting at _0_."
        seeded = seed.seed_fields(book(narrator="Jeff Hays, Annie Ellicott"))
        indices = sorted(
            {
                int(key.split(".")[2])
                for key, _ in seeded
                if key.startswith("artist_credit.names.")
            }
        )
        self.assertEqual(indices, list(range(len(indices))))


class TestNarratorParsing(unittest.TestCase):
    def test_splits_on_commas_and_strips(self):
        self.assertEqual(
            seed.narrators(book(narrator=" Jeff Hays ,Annie Ellicott ")),
            ["Jeff Hays", "Annie Ellicott"],
        )

    def test_absent_narrator_is_an_empty_list(self):
        self.assertEqual(seed.narrators(book(narrator=None)), [])

    def test_trailing_separator_does_not_produce_a_blank_name(self):
        self.assertEqual(seed.narrators(book(narrator="Jeff Hays,")), ["Jeff Hays"])

    def test_drops_placeholders_that_are_not_people(self):
        # "Stain: Everybody Loves Large Chests, Vol. 8" is tagged
        # "…, Gary Furlong, Soundbooth Theater, full cast". Seeding "full cast"
        # would create a junk artist in a public database.
        self.assertEqual(
            seed.narrators(book(narrator="Jeff Hays, full cast")), ["Jeff Hays"]
        )

    def test_drops_placeholders_case_insensitively(self):
        self.assertEqual(
            seed.narrators(book(narrator="Jeff Hays, Full Cast")), ["Jeff Hays"]
        )

    def test_keeps_a_studio_which_is_a_real_entity(self):
        self.assertIn(
            "Soundbooth Theater",
            seed.narrators(book(narrator="Jeff Hays, Soundbooth Theater")),
        )

    def test_a_dropped_placeholder_is_reported_for_review(self):
        self.assertEqual(
            seed.dropped_narrators(book(narrator="Jeff Hays, full cast")), ["full cast"]
        )

    def test_nothing_is_reported_when_every_narrator_is_a_person(self):
        self.assertEqual(seed.dropped_narrators(book()), [])


class TestReleaseShape(unittest.TestCase):
    def test_is_an_audiobook_release(self):
        self.assertEqual(fields()["type"], ["Other", "Audiobook"])

    def test_is_one_track_holding_the_whole_book(self):
        seeded = fields()
        self.assertEqual(seeded["mediums.0.format"], "Digital Media")
        self.assertEqual(seeded["mediums.0.track.0.name"], "Death Cultivator")
        self.assertEqual(seeded["mediums.0.track.0.length"], "33760467")
        self.assertNotIn("mediums.0.track.1.name", seeded)

    def test_track_title_matches_the_release_title(self):
        seeded = fields()
        self.assertEqual(seeded["mediums.0.track.0.name"], seeded["name"])

    def test_an_audible_download_has_no_barcode(self):
        self.assertEqual(fields()["barcode"], "none")

    def test_seeds_the_release_date_in_parts(self):
        seeded = fields()
        self.assertEqual(seeded["events.0.date.year"], "2020")
        self.assertEqual(seeded["events.0.date.month"], "10")
        self.assertEqual(seeded["events.0.date.day"], "11")

    def test_a_year_only_date_seeds_only_the_year(self):
        seeded = fields(date={"year": 2022})
        self.assertEqual(seeded["events.0.date.year"], "2022")
        self.assertNotIn("events.0.date.month", seeded)

    def test_an_undated_book_seeds_no_event(self):
        self.assertNotIn("events.0.date.year", fields(date=None))

    def test_an_unmapped_language_is_omitted_rather_than_guessed(self):
        self.assertNotIn("language", fields(language=None))

    def test_seeds_the_audible_product_url(self):
        # The URL relationship is what memorialises the Audible source in a
        # queryable form; the annotation is only free text. Both releases of
        # this kind already in MusicBrainz carry it as "purchase for download".
        self.assertEqual(
            fields()["urls.0.url"], "https://www.audible.com/pd/B08M3S7ZPF"
        )

    def test_leaves_the_link_type_for_the_editor_to_choose(self):
        # Documented as optional: "if left blank, can be selected in the release
        # editor". The integer ID is not published anywhere reachable, and a
        # mis-typed relationship in a public database is worse than one click.
        self.assertNotIn("urls.0.link_type", fields())

    def test_seeds_no_url_without_an_asin(self):
        self.assertNotIn("urls.0.url", fields(asin=None))

    def test_seeds_no_url_for_an_asin_of_unknown_provenance(self):
        # An ASIN that did not come from the AUDIBLE_ASIN atom might be
        # Amazon's, and pointing audible.com/pd at an Amazon ASIN would be a
        # wrong relationship in a public database.
        self.assertNotIn("urls.0.url", fields(asin_source="unknown"))

    def test_an_unknown_provenance_asin_is_still_recorded_in_the_annotation(self):
        # Free text can be honest about the uncertainty where a relationship
        # cannot, so the identifier is not simply lost.
        annotation = fields(asin_source="unknown")["annotation"]
        self.assertIn("B08M3S7ZPF", annotation)
        self.assertNotIn("Audible ASIN", annotation)

    def test_never_seeds_an_amazon_url(self):
        # The `amazon asin` relationship carries Amazon's ASIN, which is a
        # different identifier from Audible's — B01L082SCI against B01L082HJ2
        # for the same book. Only the Audible one is in these tags.
        for key, value in seed.seed_fields(book()):
            self.assertNotIn("amazon.", value)

    def test_every_seeded_value_is_a_string(self):
        for key, value in seed.seed_fields(book()):
            self.assertIsInstance(value, str, key)

    def test_no_empty_values_are_seeded(self):
        for key, value in seed.seed_fields(book(publisher=None, series=None)):
            self.assertNotEqual(value, "", key)


class TestProvenance(unittest.TestCase):
    def test_the_edit_note_cites_the_asin(self):
        self.assertIn("B08M3S7ZPF", fields()["edit_note"])

    def test_the_edit_note_does_not_claim_audible_for_an_unattributed_asin(self):
        note = fields(asin_source="unknown")["edit_note"]
        self.assertNotIn("Audible", note)
        self.assertIn("B08M3S7ZPF", note)

    def test_the_edit_note_survives_a_missing_asin(self):
        note = fields(asin=None)["edit_note"]
        self.assertIn("audiobook file", note)
        self.assertNotIn("None", note)

    def test_the_annotation_records_the_asin_and_series(self):
        annotation = fields()["annotation"]
        self.assertIn("Audible ASIN: B08M3S7ZPF", annotation)
        self.assertIn("Series: Death Cultivator, book 1", annotation)

    def test_narrators_are_never_repeated_in_the_annotation(self):
        # Every narrator is in the artist credit now, so restating the cast in
        # the annotation would duplicate it.
        self.assertNotIn("Narrated by", fields(narrator="A, B, C, D")["annotation"])

    def test_a_bare_book_seeds_no_annotation(self):
        self.assertNotIn(
            "annotation", fields(asin=None, series=None, narrator="Travis Baldree")
        )


class TestCoverArtStep(unittest.TestCase):
    """The Cover Art Archive has no upload API and no form seeding, so the most
    that can be automated is: know whether art is needed, have the image ready
    as a real file, and land on the right page."""

    def test_links_to_the_add_cover_art_page_for_the_release(self):
        self.assertEqual(
            seed.cover_art_url("93d306d6-a23a-4bec-bcb3-3098f8f25ac7"),
            "https://musicbrainz.org/release/93d306d6-a23a-4bec-bcb3-3098f8f25ac7/add-cover-art",
        )

    def test_a_book_with_embedded_art_needs_the_step(self):
        self.assertTrue(seed.has_art(book()))

    def test_a_book_with_only_a_sidecar_needs_the_step(self):
        self.assertTrue(
            seed.has_art(book(art={"embedded": False, "sidecars": ["cover.jpg"]}))
        )

    def test_a_book_with_no_art_skips_the_step(self):
        self.assertFalse(seed.has_art(book(art={"embedded": False, "sidecars": []})))

    def test_a_book_with_no_art_key_skips_the_step(self):
        # Manifests written before art was recorded must not crash the queue.
        stale = book()
        del stale["art"]
        self.assertFalse(seed.has_art(stale))

    def test_the_saved_filename_is_derived_from_the_asin(self):
        self.assertEqual(seed.cover_filename(book()), "B08M3S7ZPF.jpg")

    def test_the_saved_filename_falls_back_to_the_title(self):
        name = seed.cover_filename(book(asin=None, title="Death Cultivator"))
        self.assertEqual(name, "Death Cultivator.jpg")

    def test_the_saved_filename_is_safe_for_a_filesystem(self):
        name = seed.cover_filename(book(asin=None, title="Goroth: Book 7/8"))
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)

    def test_the_extension_follows_the_mime_type(self):
        art = {"embedded": True, "mime": "image/png", "sidecars": []}
        self.assertTrue(seed.cover_filename(book(art=art)).endswith(".png"))

    def test_the_upload_link_points_at_the_add_cover_art_page(self):
        markup = seed.cover_art_link("93d306d6-a23a-4bec-bcb3-3098f8f25ac7", "Upload")
        self.assertIn(
            'href="https://musicbrainz.org/release/'
            '93d306d6-a23a-4bec-bcb3-3098f8f25ac7/add-cover-art"',
            markup,
        )

    def test_the_upload_link_opens_in_a_new_tab(self):
        # The upload form never redirects back, so following it in this tab
        # loses the queue. Unlike the seed hand-off, which does come back.
        markup = seed.cover_art_link("93d306d6-a23a-4bec-bcb3-3098f8f25ac7", "Upload")
        self.assertIn('target="_blank"', markup)

    def test_the_upload_link_cannot_reach_back_through_the_opener(self):
        markup = seed.cover_art_link("93d306d6-a23a-4bec-bcb3-3098f8f25ac7", "Upload")
        self.assertIn("noopener", markup)


class TestSeedPage(unittest.TestCase):
    """The hand-off to MusicBrainz has to be a real link, so it can be
    middle-clicked into a new tab. A submit button cannot be, and seeding is
    documented as POST only — so the link points at a local page that posts."""

    def test_the_handoff_is_an_anchor_not_a_submit_button(self):
        markup = seed.seed_link(3, "Open in MusicBrainz")
        self.assertIn('href="/seed/3"', markup)
        self.assertTrue(markup.lstrip().startswith("<a "))

    def test_the_link_is_not_forced_into_a_new_tab(self):
        # target="_blank" would take the choice away again.
        self.assertNotIn("target=", seed.seed_link(3, "Open in MusicBrainz"))

    def test_the_posting_page_targets_musicbrainz(self):
        markup = seed.seed_form(book())
        self.assertIn('action="https://musicbrainz.org/release/add"', markup)
        self.assertIn('method="POST"', markup)

    def test_the_posting_page_carries_every_seeded_field(self):
        markup = seed.seed_form(book())
        for key, value in seed.seed_fields(book()):
            self.assertIn(f'name="{key}"', markup)
        self.assertIn("Eden Hudson", markup)

    def test_the_posting_page_carries_the_redirect(self):
        markup = seed.seed_form(book(), redirect="http://127.0.0.1:8787/captured/3")
        self.assertIn('name="redirect_uri"', markup)
        self.assertIn("captured/3", markup)

    def test_the_posting_page_submits_itself(self):
        self.assertIn("submit()", seed.seed_form(book()))

    def test_the_posting_page_works_without_javascript(self):
        # An auto-submit that silently does nothing would look like a hang.
        self.assertIn("<noscript>", seed.seed_form(book()))

    def test_values_are_escaped(self):
        markup = seed.seed_form(book(title='Quote " and <tag>'))
        self.assertNotIn('value="Quote " and', markup)
        self.assertIn("&quot;", markup)


class TestSaveCover(unittest.TestCase):
    """The page tells the user where the file is, so the file has to be there
    before it says so. Writing it as a side effect of the browser fetching the
    thumbnail made the claim true only if that request happened to succeed."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def save(self, fetcher, **overrides):
        return seed.save_cover(
            book(**overrides), self.directory, "/audio/import", "pod", fetcher=fetcher
        )

    def test_writes_the_file_and_returns_its_path(self):
        path = self.save(lambda *a, **k: b"\xff\xd8\xffdata")
        self.assertEqual(path, os.path.join(self.directory, "B08M3S7ZPF.jpg"))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"\xff\xd8\xffdata")

    def test_creates_the_directory(self):
        nested = os.path.join(self.directory, "covers")
        seed.save_cover(
            book(), nested, "/audio/import", "pod", fetcher=lambda *a, **k: b"x"
        )
        self.assertTrue(os.path.isdir(nested))

    def test_returns_none_when_the_art_cannot_be_read(self):
        self.assertIsNone(self.save(lambda *a, **k: None))

    def test_writes_nothing_when_the_art_cannot_be_read(self):
        self.save(lambda *a, **k: None)
        self.assertEqual(os.listdir(self.directory), [])

    def test_does_not_refetch_a_file_it_already_has(self):
        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return b"data"

        self.save(counting)
        self.save(counting)
        self.assertEqual(len(calls), 1)

    def test_a_book_with_no_art_is_not_fetched(self):
        called = []
        path = self.save(
            lambda *a, **k: called.append(1) or b"x",
            art={"embedded": False, "sidecars": []},
        )
        self.assertIsNone(path)
        self.assertEqual(called, [])


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "ledger.json")

    def test_records_and_persists(self):
        ledger = seed.Ledger(self.path)
        ledger.record("Cradle/unsouled.m4b", "abc-123")
        self.assertEqual(
            seed.Ledger(self.path).entries, {"Cradle/unsouled.m4b": "abc-123"}
        )

    def test_a_missing_ledger_starts_empty(self):
        self.assertEqual(seed.Ledger(self.path).entries, {})

    def test_writing_is_atomic_enough_to_reread(self):
        ledger = seed.Ledger(self.path)
        for index in range(5):
            ledger.record(f"book-{index}.m4b", f"mbid-{index}")
        with open(self.path) as handle:
            self.assertEqual(len(json.load(handle)), 5)

    def test_recording_the_same_book_twice_overwrites(self):
        ledger = seed.Ledger(self.path)
        ledger.record("a.m4b", "first")
        ledger.record("a.m4b", "second")
        self.assertEqual(ledger.entries["a.m4b"], "second")

    def test_non_ascii_paths_survive_a_round_trip(self):
        ledger = seed.Ledger(self.path)
        ledger.record("추공/book.m4b", "mbid")
        self.assertIn("추공/book.m4b", seed.Ledger(self.path).entries)


if __name__ == "__main__":
    unittest.main()
