# How to look inside the archive bucket

Everything the capture jobs collect lives in one S3-compatible bucket. Two ways
to look at it: a point-and-click app, or the command line.

## 1. Cyberduck (click around like Finder)

Installed, with a bookmark already set up.

1. Open **Cyberduck** (in Applications).
2. Double-click the **Plover Analytics Archive** bookmark.
3. It asks for a password once. That is the S3 secret key. Get it with:
   ```bash
   railway variables --service edinet-capture-job --kv | grep EDINET_S3_SECRET
   ```
   Copy the part after the `=`. Tick "Add to Keychain" so it never asks again.
4. Browse the folders. Double-click a file to download it.

## 2. rclone (command line, better for bulk)

Installed and configured as the remote `archive`. The bucket name is long, so
set a shortcut first:

```bash
B=archive:edinet-archive-5wzaqcsipd
```

Then:

**Do not paste `#` comments into the terminal.** Interactive zsh does not treat
`#` as a comment, so it passes the words as arguments and the command fails.
Every command below is comment-free on purpose.

Top-level folders:

```bash
rclone lsd $B
```

Every day of US filings:

```bash
rclone lsd $B/edgar/docs
```

One day's filings, with sizes:

```bash
rclone ls $B/edgar/docs/2026-09-09/
```

Download a folder to your Mac:

```bash
rclone copy $B/us/2026-09-10/fed ~/Downloads/fed
```

Read a compressed US filing without downloading it:

```bash
rclone cat $B/edgar/docs/2026-09-09/0000003545-26-000047.txt.gz | gunzip | head -50
```

**Sizes are slow on `edgar/`.** It holds over two million objects and `rclone
size` walks every one, which takes many minutes. Add `--fast-list` to make it
bearable, or size a single day instead:

```bash
rclone size --fast-list $B/us
rclone size $B/edgar/docs/2026-09-09/
```

`rclone ncdu $B` gives an interactive size browser. Same caveat: let it sit on
the `edgar` folder, or point it at a subfolder.

## What is in each folder

| Folder | What |
| --- | --- |
| `docs/` | EDINET filings (Japan), `YYYY-MM-DD/{docID}_t1.zip` full XBRL, `_t5.zip` CSV package |
| `lists/` | EDINET daily index, one JSON per day |
| `meta/` | one small JSON per EDINET document: SHA-256, size, filer, type |
| `tdnet/` | TDnet timely disclosures (Japan), PDFs and XBRL, `tdnet/docs/YYYY-MM-DD/` |
| `boj/` | Bank of Japan snapshots, `boj/{date}/{database}/` |
| `edgar/` | SEC filings (US), `edgar/docs/YYYY-MM-DD/{accession}.txt.gz`, gzipped |
| `us/` | US official data snapshots, `us/{date}/{source}/`, stored only when changed |

EDGAR files are gzipped: `gunzip` them, or open with any tool that reads `.gz`.
EDINET files are zips. Both keep their original bytes, verified by SHA-256.
