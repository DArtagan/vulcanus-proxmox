"""Tests for review_threads.py, which reads and acts on pull request comment threads.

Run with `python3 -m unittest discover tools/review`.

The property that matters is **that a thread can still be located after it has
been acted on**. GitHub drops `line` to null once a thread goes outdated — which
happens the moment a fix is pushed — and keeps the position only in
`originalLine`. Verified 2026-08-27 against `cli/cli` PR 14215 and reproduced on
a throwaway repo: pushing a fix flipped `isOutdated` to true and `line` to null
while `originalLine` stayed at 2.

That is precisely the wrong thing to get wrong. The threads that have been acted
on are the ones a polish pass cares about, so an implementation that reads `line`
alone loses exactly the threads it most needs to show, and looks correct while
doing it — every *un*acted thread still resolves fine.

A thread attached to a whole file rather than a line has both fields null, so the
fallback must tolerate that rather than assume `originalLine` is always present.
"""

import unittest

from review_threads import (GraphQLError, Thread, format_threads, parse_threads,
                            raise_for_graphql_errors, unresolved)


def payload(*nodes):
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(nodes)}}}}}


def node(thread_id="PRRT_x", resolved=False, outdated=False, path="a.yaml",
         line=1, original_line=1, comments=(("will", "why?"),)):
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "originalLine": original_line,
        "comments": {"nodes": [{"author": {"login": a}, "body": b} for a, b in comments]},
    }


class ParseThreads(unittest.TestCase):
    def test_uses_line_when_present(self):
        threads = parse_threads(payload(node(line=12, original_line=9)))
        self.assertEqual(threads[0].line, 12)

    def test_falls_back_to_original_line_when_outdated(self):
        threads = parse_threads(payload(node(outdated=True, line=None, original_line=7)))
        self.assertEqual(threads[0].line, 7)
        self.assertTrue(threads[0].is_outdated)

    def test_tolerates_file_level_thread_with_no_line_at_all(self):
        threads = parse_threads(payload(node(line=None, original_line=None)))
        self.assertIsNone(threads[0].line)

    def test_captures_author_and_body(self):
        threads = parse_threads(payload(node(comments=(("will", "why 30d?"),))))
        self.assertEqual(threads[0].comments, [("will", "why 30d?")])

    def test_tolerates_thread_with_no_comments(self):
        n = node()
        n["comments"]["nodes"] = []
        threads = parse_threads(payload(n))
        self.assertEqual(threads[0].comments, [])


class GraphQLErrors(unittest.TestCase):
    """GitHub reports GraphQL failures in the response body, sometimes alongside a
    zero exit status and a partially populated `data` key. Letting that through
    surfaces later as a confusing KeyError far from the cause."""

    def test_raises_when_the_body_carries_errors(self):
        body = {"data": {"repository": None},
                "errors": [{"message": "Could not resolve to a Repository"}]}
        with self.assertRaises(GraphQLError) as caught:
            raise_for_graphql_errors(body)
        self.assertIn("Could not resolve", str(caught.exception))

    def test_passes_a_clean_body_through(self):
        body = payload(node())
        self.assertIs(raise_for_graphql_errors(body), body)


class Unresolved(unittest.TestCase):
    def test_excludes_resolved_threads(self):
        threads = parse_threads(payload(
            node(thread_id="open", resolved=False),
            node(thread_id="done", resolved=True),
        ))
        self.assertEqual([t.identifier for t in unresolved(threads)], ["open"])


class FormatThreads(unittest.TestCase):
    def test_marks_outdated_so_a_null_line_is_not_read_as_current(self):
        text = format_threads(parse_threads(payload(
            node(outdated=True, line=None, original_line=7, path="alloy.yaml"))))
        self.assertIn("alloy.yaml:7", text)
        self.assertIn("outdated", text)

    def test_file_level_thread_renders_without_a_line_number(self):
        text = format_threads(parse_threads(payload(
            node(line=None, original_line=None, path="alloy.yaml"))))
        self.assertIn("alloy.yaml", text)
        self.assertNotIn("alloy.yaml:", text)

    def test_empty_reports_nothing_open(self):
        self.assertIn("no open threads", format_threads([]).lower())


if __name__ == "__main__":
    unittest.main()
