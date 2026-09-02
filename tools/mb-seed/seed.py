"""A local review queue that hands books to MusicBrainz's release editor.

MusicBrainz accepts a seeded release: a form POSTed to /release/add with the
fields pre-filled, and a `redirect_uri` that comes back carrying the new
`release_mbid`. Everything that form wants is already in the tone tags, so the
work per book drops from transcribing a release by hand to reading a filled
form and pressing submit.

    mb-seed                      # serves http://127.0.0.1:8787

The ledger (path -> MBID) is what the rest of the workflow consumes: it drives
`beet modify … mb_albumid=…` for albums already in the library, and for the
inbox it is simply the record of which books are now matchable.

The release model is one track per book, matching the Audible download as
delivered. A chapter tracklist would be richer, but the files are one file per
book and beets scores a 1-file album against a 61-track release badly enough
that nothing would ever match.

The Audible source is memorialised as a URL relationship, which is the only form
of it anything can query. Its `link_type` is left for the editor to pick: the
parameter is documented as optional, and the integer ID it wants is not
published anywhere reachable.

Books that have art get a second step, because the Cover Art Archive has no
upload API and its form takes no seeding — the most that can be automated is
extracting the image, saving it where a file picker can reach it, and landing on
the right page.
"""

import base64
import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ADD_RELEASE = "https://musicbrainz.org/release/add"
PORT = int(os.environ.get("MB_SEED_PORT", "8787"))
ORIGIN = f"http://127.0.0.1:{PORT}"

# MusicBrainz models an audiobook as primary type Other plus the Audiobook
# secondary type. Digital Media with no barcode is what an Audible download is;
# stating "none" stops the editor asking.
RELEASE_TYPES = ["Other", "Audiobook"]

# The phrasing this library's releases already use in MusicBrainz, across a
# whole series: "Neven Iliev narrated by Jeff Hays, Annie Ellicott & Justin
# Thomas James". Every performer is credited, however many there are.
NARRATED_BY = " narrated by "
BETWEEN_NARRATORS = ", "
BEFORE_LAST_NARRATOR = " & "

# Tag values that stand in for performers rather than naming one. Seeding these
# would create junk artists in a public database, and the guideline's rule is to
# credit the narrators on the cover — a placeholder is not one of them. Matched
# whole, so a studio like "Soundbooth Theater" is left alone.
NOT_A_PERSON = {
    "full cast",
    "a full cast",
    "and a full cast",
    "et al",
    "et al.",
    "and others",
    "others",
    "various",
    "various narrators",
}


def narrators(book):
    """The performers to credit, in tag order, placeholders removed."""
    return [n for n in _narrator_tokens(book) if n.lower() not in NOT_A_PERSON]


def dropped_narrators(book):
    """Placeholders removed from the credit, surfaced on the review page."""
    return [n for n in _narrator_tokens(book) if n.lower() in NOT_A_PERSON]


def _narrator_tokens(book):
    raw = book.get("narrator") or ""
    return [n.strip() for n in raw.split(",") if n.strip()]


def artist_credit(book):
    """Author, then every narrator, in this library's established phrasing.

    Seeded as separate names rather than one string on purpose: MusicBrainz
    stores the credit as an ordered list, and the `$author` inline field in the
    beets config resolves an audiobook's author from `albumartists[0]`. A single
    joined name would fall through to its regex fallback instead.
    """
    if not book.get("author"):
        return []

    names = [{"artist.name": book["author"]}]
    cast = narrators(book)
    if not cast:
        return names

    names[0]["join_phrase"] = NARRATED_BY
    for index, narrator in enumerate(cast):
        entry = {"artist.name": narrator}
        remaining = len(cast) - index - 1
        if remaining == 1:
            entry["join_phrase"] = BEFORE_LAST_NARRATOR
        elif remaining > 1:
            entry["join_phrase"] = BETWEEN_NARRATORS
        names.append(entry)
    return names


EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif"}

# Anything a path separator or a Windows-hostile character; the cover is written
# to a real file so the upload form's file picker has something to point at.
UNSAFE_IN_FILENAME = re.compile(r'[/\\:*?"<>|]')


def cover_art_url(mbid):
    """The Cover Art Archive has no upload API and the form takes no seeding,
    so landing on the right page is the whole of what can be automated."""
    return f"https://musicbrainz.org/release/{mbid}/add-cover-art"


def cover_art_link(mbid, label):
    """The upload hand-off, forced into a new tab.

    The opposite of `seed_link`: the release editor takes a `redirect_uri` and
    comes back here, while the cover art form has no such parameter and ends on
    the release page. Following it in this tab would abandon the queue.
    """
    return (
        f'<a class="button" target="_blank" rel="noopener" '
        f'href="{html.escape(cover_art_url(mbid), quote=True)}">'
        f"{html.escape(label)}</a>"
    )


def has_art(book):
    art = book.get("art") or {}
    return bool(art.get("embedded") or art.get("sidecars"))


def cover_filename(book):
    art = book.get("art") or {}
    stem = book.get("asin") or book.get("title") or "cover"
    return UNSAFE_IN_FILENAME.sub("_", stem) + EXTENSIONS.get(art.get("mime"), ".jpg")


def render_credit(credit):
    """The credit as MusicBrainz will display it, for the review page."""
    return "".join(n.get("artist.name", "") + n.get("join_phrase", "") for n in credit)


def seed_fields(book):
    """Build the flat form fields the release editor expects."""
    fields = []

    def add(key, value):
        if value not in (None, ""):
            fields.append((key, str(value)))

    add("name", book.get("title"))
    for release_type in RELEASE_TYPES:
        add("type", release_type)

    for index, name in enumerate(artist_credit(book)):
        for key, value in name.items():
            add(f"artist_credit.names.{index}.{key}", value)

    add("mediums.0.format", "Digital Media")
    add("mediums.0.track.0.name", book.get("title"))
    add("mediums.0.track.0.length", book.get("length_ms"))

    date = book.get("date") or {}
    add("events.0.date.year", date.get("year"))
    add("events.0.date.month", date.get("month"))
    add("events.0.date.day", date.get("day"))

    add("language", book.get("language"))
    add("script", "Latn")
    add("status", "Official")
    add("packaging", "None")
    add("barcode", "none")
    add("labels.0.name", book.get("publisher"))

    # The relationship is what memorialises the Audible source in a form
    # anything can query; the annotation below is only free text. `link_type` is
    # documented as optional and is left for the editor, where "purchase for
    # download" is one click — the integer ID it wants is not published.
    #
    # Only ever the Audible URL, and only for an identifier that said it was
    # Audible's. MusicBrainz's `amazon asin` relationship holds Amazon's ASIN,
    # a different namespace for the same book, so pointing audible.com/pd at an
    # identifier of unknown origin would assert something untrue.
    if book.get("asin") and book.get("asin_source") == "audible":
        add("urls.0.url", f"https://www.audible.com/pd/{book['asin']}")

    # Free text can be honest about an uncertain vendor where a relationship
    # cannot, so an unattributable identifier is recorded rather than dropped.
    annotation = []
    if book.get("asin"):
        if book.get("asin_source") == "audible":
            annotation.append(f"Audible ASIN: {book['asin']}")
        else:
            annotation.append(f"ASIN (vendor unrecorded in the file): {book['asin']}")
    if book.get("series"):
        series = book["series"]
        if book.get("part"):
            series += f", book {book['part']}"
        annotation.append(f"Series: {series}")
    add("annotation", "\n".join(annotation))

    if not book.get("asin"):
        source = "the audiobook file"
    elif book.get("asin_source") == "audible":
        source = f"Audible ASIN {book['asin']}"
    else:
        source = f"the audiobook file, ASIN {book['asin']}"
    add("edit_note", f"Audiobook release, metadata taken from {source}.")
    return fields


def fetch_cover(book, root, pod, namespace="apps"):
    """Pull one book's cover out of the cluster, embedded art or sidecar.

    Base64 over `kubectl exec`, because raw bytes through that pipe truncate
    silently — the same failure that cost three attempts at copying the
    databases out. The length is checked against what the manifest recorded.
    """
    art = book.get("art") or {}
    source = os.path.join(root, book["path"])
    if art.get("embedded"):
        reader = (
            "import base64,sys,mutagen;"
            "t=mutagen.File(sys.argv[1]).tags;"
            "sys.stdout.write(base64.b64encode(bytes(t['covr'][0])).decode())"
        )
    elif art.get("sidecars"):
        source = os.path.join(root, book["folder"], art["sidecars"][0])
        reader = (
            "import base64,sys;"
            "sys.stdout.write(base64.b64encode(open(sys.argv[1],'rb').read()).decode())"
        )
    else:
        return None

    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            namespace,
            "-c",
            "beets-flask",
            pod,
            "--",
            "/venv/bin/python",
            "-c",
            reader,
            source,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    data = base64.b64decode(result.stdout)
    expected = art.get("bytes")
    if art.get("embedded") and expected and len(data) != expected:
        return None
    return data


def seed_link(index, label):
    """The hand-off to MusicBrainz, as a link rather than a submit button.

    Seeding is documented as POST only, and a submit button cannot be
    middle-clicked into a new tab — so the link points at a local page that
    does the POST on arrival. Left-click, middle-click and ctrl-click all
    behave the way the browser's own rules say they should, which is why no
    `target` is set here.
    """
    return f'<a class="button" href="/seed/{index}">{html.escape(label)}</a>'


def seed_form(book, redirect=None):
    """A page whose only job is to POST the seed to MusicBrainz on arrival."""
    inputs = "".join(
        f'<input type="hidden" name="{html.escape(key)}" '
        f'value="{html.escape(value, quote=True)}">'
        for key, value in seed_fields(book)
    )
    if redirect:
        inputs += (
            f'<input type="hidden" name="redirect_uri" '
            f'value="{html.escape(redirect, quote=True)}">'
        )
    return (
        f'<form id="seed" method="POST" action="{ADD_RELEASE}">{inputs}'
        f"<noscript><p>JavaScript is off, so this did not submit itself.</p>"
        f'<button type="submit">Continue to MusicBrainz</button></noscript>'
        f"</form><script>document.getElementById('seed').submit()</script>"
        f"<p>Opening MusicBrainz&hellip;</p>"
    )


def save_cover(book, covers_dir, root, pod, fetcher=fetch_cover):
    """Put the art on disk and return where, or None if it could not be read.

    Called before the page renders rather than when the browser asks for the
    thumbnail: the page tells the user where the file is, and that has to be
    true when it says it.
    """
    if not has_art(book):
        return None
    path = os.path.join(covers_dir, cover_filename(book))
    if os.path.exists(path):
        return path
    data = fetcher(book, root, pod)
    if data is None:
        return None
    os.makedirs(covers_dir, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class Ledger:
    def __init__(self, path):
        self.path = path
        self.entries = {}
        if os.path.exists(path):
            with open(path) as handle:
                self.entries = json.load(handle)

    def record(self, book_path, mbid):
        self.entries[book_path] = mbid
        with open(self.path, "w") as handle:
            json.dump(
                self.entries, handle, indent=1, ensure_ascii=False, sort_keys=True
            )
            handle.write("\n")


def page(title, body):
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 46rem; margin: 2rem auto;
        padding: 0 1rem; color: #222; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
 td, th {{ text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #eee;
          vertical-align: top; }}
 th {{ width: 10rem; color: #666; font-weight: normal; }}
 .warn {{ background: #fff4e5; border-left: 3px solid #e08000; padding: .6rem .8rem;
         margin: 1rem 0; }}
 .done {{ color: #157f3b; }}
 button {{ font-size: 1rem; padding: .6rem 1.2rem; cursor: pointer; }}
 a {{ color: #06c; }}
 /* A link styled as a button, so middle-click and ctrl-click open a new tab
    the way they would anywhere else. A submit button cannot do that. */
 a.button {{ display: inline-block; font-size: 1rem; padding: .6rem 1.2rem;
            background: #06c; color: #fff; border-radius: .3rem;
            text-decoration: none; }}
 a.button:hover {{ background: #05a; }}
 code {{ background: #f4f4f4; padding: .1rem .3rem; }}
</style>
{body}"""


class Handler(BaseHTTPRequestHandler):
    books = []
    ledger = None
    covers_dir = ""
    root = "/audio/import"
    pod = ""

    def log_message(self, *args):
        pass

    def reply(self, body, status=200):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)

        if not parts:
            return self.reply(self.index())
        if parts[0] == "book" and len(parts) == 2:
            return self.reply(self.book(int(parts[1])))
        if parts[0] == "captured" and len(parts) == 2:
            index = int(parts[1])
            mbid = (query.get("release_mbid") or [""])[0]
            if mbid:
                self.ledger.record(self.books[index]["path"], mbid)
            return self.redirect(self.after_capture(index))
        if parts[0] == "record" and len(parts) == 2:
            index = int(parts[1])
            mbid = (query.get("mbid") or [""])[0].strip()
            if mbid:
                self.ledger.record(self.books[index]["path"], mbid)
            return self.redirect(self.after_capture(index))
        if parts[0] == "seed" and len(parts) == 2:
            index = int(parts[1])
            return self.reply(
                page(
                    "Opening MusicBrainz",
                    seed_form(self.books[index], f"{ORIGIN}/captured/{index}"),
                )
            )
        if parts[0] == "cover" and len(parts) == 2:
            return self.reply(self.cover(int(parts[1])))
        if parts[0] == "cover-image" and len(parts) == 2:
            return self.serve_cover(int(parts[1]))
        return self.reply(page("Not found", "<h1>Not found</h1>"), 404)

    def after_capture(self, index):
        """Cover art is a second visit to MusicBrainz, so it gets its own step
        rather than being lost between one book and the next."""
        book = self.books[index]
        if has_art(book) and self.ledger.entries.get(book["path"]):
            return f"/cover/{index}"
        return self.next_after(index)

    def save_cover(self, index):
        return save_cover(self.books[index], self.covers_dir, self.root, self.pod)

    def serve_cover(self, index):
        book = self.books[index]
        path = self.save_cover(index)
        if path is None:
            return self.reply(page("No art", "<h1>Could not read the art</h1>"), 404)
        with open(path, "rb") as handle:
            data = handle.read()
        mime = (book.get("art") or {}).get("mime") or "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def cover(self, index):
        book = self.books[index]
        mbid = self.ledger.entries.get(book["path"], "")
        art = book.get("art") or {}
        source = (
            "embedded in the file"
            if art.get("embedded")
            else f"sidecar {art['sidecars'][0]}"
            if art.get("sidecars")
            else "none"
        )
        # Written before rendering, so "Saved to" is a fact rather than a plan.
        # The Cover Art Archive takes no seeding and has no API, so a real file
        # the upload form's picker can reach is the whole of the help available.
        saved = self.save_cover(index)
        if saved is None:
            body = (
                '<div class="warn"><b>Could not read the art.</b> The file may '
                "have left the inbox since the manifest was built, or beets-flask "
                "may have restarted onto a new pod — rerun <code>mb-seed</code> to "
                "refresh both.</div>"
            )
        else:
            body = (
                f"<p><img src='/cover-image/{index}' style='max-width:22rem;"
                f"border:1px solid #ddd' alt='cover'></p>"
                f"<table><tr><th>Source</th><td>{html.escape(source)}</td></tr>"
                f"<tr><th>Size</th><td>{(art.get('bytes') or 0) // 1024} KiB</td></tr>"
                f"<tr><th>Saved to</th><td><code>{html.escape(saved)}</code></td>"
                f"</tr></table>"
                f"<p>{cover_art_link(mbid, 'Upload to the Cover Art Archive →')}</p>"
                f"<p style='color:#666'>Choose <b>Front</b> as the type.</p>"
            )
        return page(
            f"Cover art — {book.get('title')}",
            f"<p><a href='/'>&larr; queue</a></p>"
            f"<h1>Cover art</h1>"
            f"<p>{html.escape(book.get('title') or book['path'])}</p>"
            f"{body}"
            f"<p><a href='{self.next_after(index)}'>Continue to the next book &rarr;</a></p>",
        )

    def next_after(self, index):
        for candidate in range(index + 1, len(self.books)):
            if self.books[candidate]["path"] not in self.ledger.entries:
                return f"/book/{candidate}"
        return "/"

    def index(self):
        done = len(self.ledger.entries)
        rows = []
        for index, book in enumerate(self.books):
            mbid = self.ledger.entries.get(book["path"])
            state = (
                f'<span class="done">submitted</span> '
                f'<a href="https://musicbrainz.org/release/{mbid}">{mbid[:8]}…</a>'
                if mbid
                else f'<a href="/book/{index}">review &amp; seed</a>'
            )
            flags = " ".join(f"<code>{html.escape(w)}</code>" for w in book["warnings"])
            rows.append(
                f"<tr><td>{html.escape(book.get('title') or book['path'])}</td>"
                f"<td>{html.escape(book.get('author') or '—')}</td>"
                f"<td>{state}</td><td>{flags}</td></tr>"
            )
        return page(
            "mb-seed",
            f"<h1>mb-seed</h1><p>{done} of {len(self.books)} submitted.</p>"
            f"<table><tr><th>Title</th><th>Author</th><th>State</th><th></th></tr>"
            + "".join(rows)
            + "</table>",
        )

    def book(self, index):
        book = self.books[index]
        rendered = render_credit(artist_credit(book))

        rows = "".join(
            f"<tr><th>{key}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in [
                ("Release", book.get("title")),
                ("Artist credit", rendered),
                ("Length", f"{(book.get('length_ms') or 0) / 3600000:.1f} h"),
                ("Date", book.get("date")),
                ("Publisher", book.get("publisher")),
                ("ASIN", book.get("asin")),
                ("Series", book.get("series")),
                ("File", book["path"]),
            ]
            if value
        )

        warnings = ""
        if book["warnings"]:
            warnings = (
                '<div class="warn"><b>Check before submitting:</b><ul>'
                + "".join(f"<li>{html.escape(w)}</li>" for w in book["warnings"])
                + "</ul></div>"
            )

        dropped = dropped_narrators(book)
        if dropped:
            warnings += (
                '<div class="warn">Dropped from the credit as placeholders rather '
                "than people: " + html.escape(", ".join(dropped)) + ". Check the "
                "cover credits if any of them names a real performer.</div>"
            )

        return page(
            book.get("title") or book["path"],
            f"<p><a href='/'>&larr; queue</a> &middot; {index + 1} of {len(self.books)}</p>"
            f"<h1>{html.escape(book.get('title') or book['path'])}</h1>"
            f"{warnings}<table>{rows}</table>"
            f"<p>{seed_link(index, 'Open in MusicBrainz →')}</p>"
            f"<p style='margin-top:2rem;color:#666'>If MusicBrainz does not send you "
            f"back here after submitting, paste the release MBID:</p>"
            f"<form method='GET' action='/record/{index}'>"
            f"<input name='mbid' size='40' placeholder='release MBID'>"
            f"<button type='submit'>Record</button></form>",
        )


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    manifest_path = (
        sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "manifest.json")
    )
    with open(manifest_path) as handle:
        manifest = json.load(handle)
    books = manifest["books"]
    Handler.root = manifest.get("root", "/audio/import")
    Handler.pod = os.environ.get("MB_SEED_POD", "")
    Handler.covers_dir = os.path.join(os.path.dirname(manifest_path), "covers")

    # Remaining arguments narrow the queue by substring. The inbox is ~479
    # books and upstream lags past a few hundred, so this is how a batch gets
    # picked; with no arguments the whole backlog is offered.
    patterns = [p.lower() for p in sys.argv[2:]]
    if patterns:
        books = [
            b
            for b in books
            if any(
                p in (b.get("path", "") + (b.get("title") or "")).lower()
                for p in patterns
            )
        ]
        if not books:
            sys.exit(f"No books matched {', '.join(patterns)}")

    Handler.books = books
    Handler.ledger = Ledger(os.path.join(os.path.dirname(manifest_path), "ledger.json"))

    print(
        f"{len(Handler.books)} books; {len(Handler.ledger.entries)} already submitted"
    )
    print(f"==> {ORIGIN}")
    try:
        webbrowser.open(ORIGIN)
    except Exception:  # noqa: BLE001 - a headless shell is not a failure
        pass
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
