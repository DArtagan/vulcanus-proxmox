# book-import: Design Spec

## Purpose

A single CLI tool that is the sole entry point for adding books to the Stump library. Every book passes through the same pipeline regardless of source, enforcing consistent metadata, format, and directory structure. The goal is high data hygiene with minimal tooling.

## Library Structure

```
/books/
  Author Name/
    Series Name/           # only if book belongs to a series
      Book Title/
        Book Title.epub    # canonical reading format
        Book Title.pdf     # archival (if source was PDF)
        Book Title.azw3    # archival (if source was Kindle)
        cover.jpg
        metadata.opf       # sidecar metadata backup (OPF standard)
    Book Title/            # standalone (no series)
      ...
```

## Pipeline

```
  Input file(s)
       │
       ▼
  ┌─────────┐
  │ DeDRM   │  Strip DRM if present (AZW3/KFX/EPUB)
  └────┬────┘
       ▼
  ┌─────────┐
  │ Convert │  Produce EPUB if not already; keep original
  └────┬────┘
       ▼
  ┌─────────┐
  │ Metadata│  Resolve, confirm, embed into EPUB
  └────┬────┘
       ▼
  ┌─────────┐
  │ Place   │  Move into library with correct directory structure
  └─────────┘
```

### Stage 1: DeDRM

Strip DRM from Kindle (AZW3, KFX, MOBI) and Adobe EPUB files so they are readable on any device. Use the DeDRM tools (NoDRM fork) as a library or subprocess.

- If DRM is detected and successfully removed, proceed with the DRM-free copy.
- If DRM is detected and removal fails, abort with a clear error. Do not import DRM-locked files — they are useless outside their original ecosystem.
- If no DRM is present, pass through unchanged.
- The original DRM-locked file is never kept; only the DRM-free version enters the library.

### Stage 2: Format Conversion

Produce an EPUB if the input is not already one. Keep the original format alongside for archival.

- Use `ebook-convert` (Calibre CLI) for conversion. It handles PDF, MOBI, AZW3, DOCX, HTML, CBZ, and more.
- EPUB is the canonical format. Stump indexes it; KOReader reads it.
- Original non-EPUB files are preserved in the BookDir for archival fidelity.
- If the input is already EPUB, no conversion is needed.

### Stage 3: Metadata

Metadata must be correct before the book enters the library. This stage resolves metadata from multiple sources, presents it for confirmation, and embeds it into the EPUB.

#### Required fields

| Field         | Source priority                                    |
|---------------|----------------------------------------------------|
| Title         | User input > embedded > filename parse             |
| Author        | User input > embedded > lookup                     |
| Series        | User input > embedded > lookup                     |
| Series index  | User input > embedded > lookup                     |

#### Optional fields (populate if available)

Publisher, ISBN, publication date, description, tags, language, cover image.

#### Metadata lookup

When the user provides an ISBN (or one is found embedded), look up metadata from:

1. Open Library API (`openlibrary.org/isbn/{isbn}.json`) — free, no key needed
2. Google Books API (`googleapis.com/books/v1/volumes?q=isbn:{isbn}`) — free tier

The lookup populates any missing fields. The user always confirms before embedding.

#### Embedding

Use `ebook-meta` (Calibre CLI) to write confirmed metadata into the EPUB:
```
ebook-meta book.epub \
  --title "..." --authors "..." \
  --series "..." --index N \
  --publisher "..." --isbn "..." \
  --tags "..." --comments "..." \
  --cover cover.jpg
```

Also generate a `metadata.opf` sidecar in the BookDir as a portable backup.

#### Author normalization

Maintain an optional author alias file (`~/.config/book-import/authors.toml`):
```toml
[aliases]
"T.A. Pratt" = "Tim Pratt"
"T. Aaron Payton" = "Tim Pratt"
```

When the resolved author matches an alias, the canonical name is used for the directory and embedded metadata.

### Stage 4: Placement

Place the book in the library with the correct directory structure.

- If the book has a series: `Author/Series/BookTitle/`
- If standalone: `Author/BookTitle/`
- All files for the book go into the BookDir: EPUB, original format, cover.jpg, metadata.opf.
- If a BookDir already exists at the target path, warn and prompt (overwrite / skip / rename).
- After placement, trigger a Stump library scan (via API if available, or print a reminder).

## CLI Interface

```
book-import <file> [options]

Arguments:
  file                  Path to the ebook file to import

Options:
  --title TEXT          Override title
  --author TEXT         Override author
  --series TEXT         Series name (implies series book)
  --index FLOAT        Series index (e.g. 1, 3.5)
  --isbn TEXT          ISBN for metadata lookup
  --no-convert         Skip EPUB conversion (import original format only)
  --no-drm             Skip DRM removal step
  --dry-run            Show what would happen without modifying anything
  --library PATH       Library root (default: from config)
```

### Interactive mode

When required metadata is missing and not provided via flags, prompt interactively:

```
$ book-import ~/Downloads/new-book.azw3

  DeDRM: stripped Kindle DRM successfully
  Convert: produced EPUB from AZW3

  --- Metadata (from embedded + ISBN lookup) ---
  Title:   The Way of Kings
  Author:  Brandon Sanderson
  Series:  The Stormlight Archive
  Index:   1
  ISBN:    9780765376671
  Pub:     Tom Doherty Associates (2010)

  Accept? [Y/n/edit]

  Placed: /books/Brandon Sanderson/The Stormlight Archive/The Way of Kings/
    ├── The Way of Kings.epub
    ├── The Way of Kings.azw3
    ├── cover.jpg
    └── metadata.opf
```

### Batch mode

For importing multiple files (e.g., a Humble Bundle):

```
book-import ~/Downloads/humble-bundle/*.epub --author "Various"
```

Each file goes through the full pipeline independently. Interactive prompts appear per-book unless all metadata is resolvable automatically.

## Configuration

`~/.config/book-import/config.toml`:

```toml
library_path = "/mnt/storage/books"
author_aliases = "~/.config/book-import/authors.toml"

[dedrm]
enabled = true

[convert]
target_format = "epub"

[stump]
url = "https://stump.immortalkeep.com"
# credentials via STUMP_USERNAME / STUMP_PASSWORD env vars
```

## Dependencies

- `ebook-convert` and `ebook-meta` from Calibre (CLI only)
- DeDRM / NoDRM tools (for Kindle/Adobe DRM stripping)
- Python 3.12+ (or could be a shell script wrapping the above)
- Optional: `curl`/`httpx` for ISBN metadata lookup

## Non-goals

- No GUI. This is a CLI tool.
- No library database. Stump is the database; the filesystem is the source of truth.
- No automatic downloading or scraping of books.
- No management of reading progress or annotations (Stump + KOReader handle that).
- No bulk library reorganization (the migration script was a one-time tool).

## Open Questions

- **Cover extraction**: Should the tool extract cover images from EPUBs that have them embedded, and save as `cover.jpg`? Stump may already do this during scanning.
- **Duplicate detection**: Beyond path collision, should the tool check for ISBN or title+author matches against existing library contents? This requires scanning the library, which could be slow without an index.
- **Stump API integration**: Stump may expose an API for triggering library scans or even uploading books directly. Worth checking as the project matures.
- **KFX format**: Amazon's newer KFX format requires additional tooling (`KFX Input` Calibre plugin). Worth supporting if Kindle purchases are common.
