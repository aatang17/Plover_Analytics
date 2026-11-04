# -*- coding: utf-8 -*-
"""One document, three files: PDF, Word (.docx) and Excel (.xlsx), laid out as a
research note.

Research a Stock (app/us_research_api.py) describes its brief once, as a plain
document; each writer here lays that document out in its own format, in one
house style: the Plover masthead, the company and its headline, Key Points
beside a Key Data panel, numbered exhibits each with its source line, a
running header and page numbers, and About This Brief at the end. Only the
standard library is used: a .docx and an .xlsx are zip files of XML, and the
PDF is written by hand. IBM Plex Sans (app/doc_assets/, SIL Open Font
License) is embedded in both the PDF and the Word file, so the brief looks the
same wherever it is opened; the semibold file is named as the family's bold
so Word pairs the two.

The document:
  title, company, ticker, line (ticker · exchange · industry), kicker, date,
  subtitle (the headline), meta (lines under it), footer, blocks:
  {"t": "points", "items": [text]}                     Key Points
  {"t": "keydata", "items": [[label, value]]}          Key Data
  {"t": "h", "text"}                                   a section heading
  {"t": "p", "text", "style": "body"|"note"|"sub"|"lead"}
  {"t": "stats", "items": [[label, value, small]]}     figures side by side
  {"t": "table", "name", "title", "source", "cols": [[label, "l"|"r"]],
   "rows": [[text]], "values": [[number or None]], "formats": [excel format],
   "units": [unit], "strong": [row], "muted": [row], "note"}
  {"t": "list", "items": [text], "ordered": bool, "small": bool}
  {"t": "quote", "text", "cite"}                       words from a filing
  {"t": "links", "items": [[date, title, url]], "style"}

Missing values arrive as "—" in text and None in `values`: an Excel cell for
a missing value is left empty, never 0.
"""
import datetime
import io
import pathlib
import re
import struct
import zipfile
import zlib
from xml.sax.saxutils import escape as _xml_escape

ASSETS = pathlib.Path(__file__).resolve().parent / "doc_assets"
FONTS = ASSETS
_BAD_XML = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

# the house colours (web/assets/tokens.css, light theme)
NAVY, INK, TEXT, MUTED, RULE, GRID, PANEL, ZEBRA, ME = (
    "1A4D8F", "0F172A", "334155", "64748B", "CBD5E1", "E2E8F0", "F1F5F9", "F8FAFC", "E8EFFA")


def _x(text):
    return _xml_escape(_BAD_XML.sub("", str(text if text is not None else "")), {'"': "&quot;"})


def _safe_name(text, limit=31):
    """An Excel sheet name: no []:*?/\\, at most 31 characters."""
    return (re.sub(r"[\[\]:*?/\\]", " ", text or "Sheet").strip() or "Sheet")[:limit]


def exhibits(doc):
    """Each table's exhibit number, in reading order."""
    n, out = 0, {}
    for i, b in enumerate(doc["blocks"]):
        if b["t"] == "table" and b.get("title"):
            n += 1
            out[i] = n
    return out


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


# ============================================================== Word (.docx)
_W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
         'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
         'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
_FONT = "IBM Plex Sans"
# fixed keys for the embedded fonts (ECMA-376 Part 1, 17.8.1: the first 32 bytes are XORed with the key)
_FONT_KEYS = ("{4A1F2C3B-5D6E-4F70-8192-A3B4C5D6E7F8}", "{5B2E3D4C-6E7F-4081-92A3-B4C5D6E7F809}")
_TEXT_W = 9978            # A4 width less 1.7 cm margins, in twentieths of a point


def _obfuscate(data, key):
    raw = bytes.fromhex(key.strip("{}").replace("-", ""))[::-1]
    head = bytes(b ^ raw[i % 16] for i, b in enumerate(data[:32]))
    return head + data[32:]


def _rpr(bold=False, size=None, color=None, italic=False, caps=False, spacing=None):
    out = ""
    if bold:
        out += "<w:b/><w:bCs/>"
    if italic:
        out += "<w:i/>"
    if caps:
        out += "<w:caps/>"
    if color:
        out += '<w:color w:val="%s"/>' % color
    if spacing:
        out += '<w:spacing w:val="%d"/>' % spacing
    if size:
        out += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (round(size * 2), round(size * 2))
    return "<w:rPr>%s</w:rPr>" % out if out else ""


def _run(text, **kw):
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (_rpr(**kw), _x(text))


def _ppr(style=None, align=None, before=None, after=None, keep=False, border=None, indent=None, shade=None,
         tabs=None, num=None, line=None):
    out = ""
    if style:
        out += '<w:pStyle w:val="%s"/>' % style
    if keep:
        out += "<w:keepNext/>"
    if num:
        out += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="%d"/></w:numPr>' % num
    if border:
        out += "<w:pBdr>%s</w:pBdr>" % border
    if shade:
        out += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade
    if tabs:
        out += "<w:tabs>%s</w:tabs>" % "".join('<w:tab w:val="%s" w:pos="%d"/>' % t for t in tabs)
    if before is not None or after is not None or line:
        out += "<w:spacing%s%s%s/>" % (' w:before="%d"' % before if before is not None else "",
                                       ' w:after="%d"' % after if after is not None else "",
                                       ' w:line="%d" w:lineRule="auto"' % line if line else "")
    if indent:
        out += '<w:ind %s/>' % indent
    if align:
        out += '<w:jc w:val="%s"/>' % align
    return "<w:pPr>%s</w:pPr>" % out if out else ""


def _p(runs, **kw):
    return "<w:p>%s%s</w:p>" % (_ppr(**kw), "".join(runs) if isinstance(runs, list) else runs)


def _cell(content, width, shade=None, valign=None, margins=None, borders=None):
    tcpr = '<w:tcW w:w="%d" w:type="dxa"/>' % width
    if borders:
        tcpr += "<w:tcBorders>%s</w:tcBorders>" % borders
    if shade:
        tcpr += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade
    if margins:
        tcpr += "<w:tcMar>%s</w:tcMar>" % margins
    if valign:
        tcpr += '<w:vAlign w:val="%s"/>' % valign
    return "<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % (tcpr, content or _p([]))


_NO_BORDERS = ('<w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/>'
               '<w:insideH w:val="nil"/><w:insideV w:val="nil"/>')


def _tbl(rows_xml, widths, borders="", cell_margin=(40, 40, 70, 70)):
    t, b, l, r = cell_margin
    return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>%s</w:tblBorders>'
            '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="%d" w:type="dxa"/><w:left w:w="%d" w:type="dxa"/>'
            '<w:bottom w:w="%d" w:type="dxa"/><w:right w:w="%d" w:type="dxa"/></w:tblCellMar>'
            '<w:tblLook w:val="0000"/></w:tblPr><w:tblGrid>%s</w:tblGrid>%s</w:tbl>'
            % (sum(widths), borders or _NO_BORDERS, t, l, b, r,
               "".join('<w:gridCol w:w="%d"/>' % w for w in widths), "".join(rows_xml)))


def _widths(cols, rows, total):
    """Each column as wide as its longest cell or header word; the first (text) column takes the rest."""
    nat = [max([max(len(w) for w in (c[0] or " ").split()) * 1.15] + [len(r[i]) for r in rows]) + 2.5
           for i, c in enumerate(cols)]
    # in proportion to what each column holds; a long first (text) column is held to 40% and wraps
    whole = sum(nat)
    if nat[0] / whole > 0.4 and whole > 100:
        nat[0] = 0.4 / 0.6 * sum(nat[1:])
        whole = sum(nat)
    return [int(total * w / whole) for w in nat]


class _Word(object):
    def __init__(self, doc):
        self.doc, self.links, self.lists = doc, [], []
        self.ex = exhibits(doc)

    def link(self, text, url, size=9):
        self.links.append(url)
        return ('<w:hyperlink r:id="rL%d"><w:r><w:rPr><w:color w:val="%s"/><w:u w:val="single"/>'
                '<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:hyperlink>'
                % (len(self.links), NAVY, round(size * 2), round(size * 2), _x(text)))

    def new_list(self, ordered):
        self.lists.append(ordered)
        return len(self.lists) + 2           # 1 and 2 are the plain bullet and number definitions

    def logo(self, size_pt=20):
        emu = int(size_pt * 12700)
        return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/>'
                '<wp:docPr id="1" name="Plover Analytics"/><a:graphic><a:graphicData '
                'uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr>'
                '<pic:cNvPr id="1" name="plover-mark.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
                '<a:blip r:embed="rLogo"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr>'
                '<a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
                '</a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
                % (emu, emu, emu, emu))

    # ---- the first page
    def masthead(self):
        d = self.doc
        left = _p([self.logo(19), _run(u"\u2002Plover Analytics", bold=True, size=11.5, color=INK)], after=0)
        right = (_p([_run(d.get("kicker") or "Research Brief", bold=True, size=7.5, color=NAVY, caps=True, spacing=20)],
                    align="right", after=0) +
                 _p([_run(d.get("date") or "", size=8, color=MUTED)], align="right", after=0))
        w = [_TEXT_W // 2, _TEXT_W - _TEXT_W // 2]
        out = _tbl(["<w:tr>%s%s</w:tr>" % (_cell(left, w[0], valign="center"), _cell(right, w[1], valign="center"))],
                   w, cell_margin=(0, 0, 0, 0))
        out += _p([], border='<w:bottom w:val="single" w:sz="18" w:space="1" w:color="%s"/>' % NAVY, after=240)
        out += _p([_run(d.get("company") or d["title"], bold=True, size=24, color=INK)], after=40, line=240)
        if d.get("line"):
            out += _p([_run(d["line"], size=9, color=MUTED)], after=160)
        if d.get("subtitle"):
            out += _p([_run(d["subtitle"], bold=True, size=13, color=NAVY)], after=220, line=264)
        return out

    def points_and_data(self, points, data):
        gap = 280
        lw = int(_TEXT_W * 0.60)
        rw = _TEXT_W - lw - gap
        lid = self.new_list(False)
        left = _p([_run("Key Points", bold=True, size=8, color=NAVY, caps=True, spacing=20)], after=100)
        left += "".join(_p([_run(x, size=9.5, color=INK)], num=lid, after=110, line=264) for x in points)
        inner = rw - 320
        kd_w = [int(inner * 0.60), inner - int(inner * 0.60)]
        line = '<w:bottom w:val="single" w:sz="4" w:color="%s"/>' % GRID
        kd_rows = ["<w:tr>%s%s</w:tr>" % (
            _cell(_p([_run(k, size=8, color=TEXT)], after=0), kd_w[0], borders=line),
            _cell(_p([_run(v, bold=True, size=8.5, color=INK)], align="right", after=0), kd_w[1], borders=line))
            for k, v in data]
        right = (_p([_run("Key Data", bold=True, size=8, color=NAVY, caps=True, spacing=20)], after=80) +
                 _tbl(kd_rows, kd_w, cell_margin=(45, 45, 0, 0)) +
                 '<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/></w:pPr></w:p>')
        margins = ('<w:top w:w="160" w:type="dxa"/><w:left w:w="160" w:type="dxa"/>'
                   '<w:bottom w:w="160" w:type="dxa"/><w:right w:w="160" w:type="dxa"/>')
        row = "<w:tr>%s%s%s</w:tr>" % (_cell(left, lw), _cell(_p([]), gap), _cell(right, rw, shade=PANEL, margins=margins))
        return _tbl([row], [lw, gap, rw], cell_margin=(0, 0, 0, 0))

    # ---- the blocks
    def table(self, b, number):
        out = ""
        if b.get("title"):
            out += _p([_run("Exhibit %d: " % number, bold=True, size=8.5, color=NAVY),
                       _run(b["title"], bold=True, size=8.5, color=INK)], before=200, after=80, keep=True)
        cols, rows = b["cols"], b["rows"]
        widths = _widths(cols, rows, _TEXT_W)
        strong, muted = set(b.get("strong") or ()), set(b.get("muted") or ())
        head = "<w:tr><w:trPr><w:tblHeader/></w:trPr>%s</w:tr>" % "".join(
            _cell(_p([_run(c[0], bold=True, size=7.5, color="FFFFFF")], align="right" if c[1] == "r" else None,
                     after=0, line=228), widths[i], shade=NAVY, valign="bottom")
            for i, c in enumerate(cols))
        line = '<w:bottom w:val="single" w:sz="4" w:color="%s"/>' % GRID
        body = []
        for n, r in enumerate(rows):
            shade = ME if n in strong else (ZEBRA if n % 2 else None)
            color = MUTED if n in muted else INK
            body.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>%s</w:tr>" % "".join(
                _cell(_p([_run(v, bold=n in strong, size=8.5, color=color)], align="right" if cols[i][1] == "r" else None,
                         after=0, line=240), widths[i], shade=shade, borders=line)
                for i, v in enumerate(r)))
        out += _tbl([head] + body, widths, borders=(
            '<w:top w:val="nil"/><w:left w:val="nil"/><w:right w:val="nil"/><w:insideV w:val="nil"/>'
            '<w:bottom w:val="single" w:sz="8" w:color="%s"/><w:insideH w:val="nil"/>' % NAVY))
        notes = [x for x in (("Source: " + b["source"]) if b.get("source") else None, b.get("note")) if x]
        out += _p([_run(" ".join(notes), size=7.5, color=MUTED)], before=60, after=160) if notes else _p([], after=120)
        return out

    def block(self, i, b):
        t = b["t"]
        if t == "points":
            return ""                                  # drawn beside Key Data
        if t == "keydata":
            pts = next((x["items"] for x in self.doc["blocks"] if x["t"] == "points"), [])
            return self.points_and_data(pts, b["items"]) + _p([], after=60)
        if t == "h":
            out = _p([_run(b["text"], bold=True, size=11, color=NAVY)], before=0 if b.get("about") else 360, after=140,
                     keep=True, border='<w:bottom w:val="single" w:sz="6" w:space="3" w:color="%s"/>' % NAVY)
            # the closing section starts its own page, as a note's disclosures do
            return out.replace("<w:pPr>", "<w:pPr><w:pageBreakBefore/>", 1) if b.get("about") else out
        if t == "p":
            st = b.get("style") or "body"
            if st == "sub":
                return _p([_run(b["text"], bold=True, size=9.5, color=INK)], before=180, after=60, keep=True)
            if st == "note":
                return _p([_run(b["text"], size=8, color=MUTED)], after=100, line=252)
            if st == "lead":
                return _p([_run(b["text"], bold=True, size=12, color=INK)], after=120)
            return _p([_run(b["text"], size=9.5, color=INK)], after=120, line=276)
        if t == "stats":
            n = max(1, len(b["items"]))
            w = [_TEXT_W // n] * n
            edge = ('<w:top w:val="single" w:sz="4" w:color="%s"/><w:bottom w:val="single" w:sz="4" w:color="%s"/>'
                    % (GRID, GRID))
            cells = "".join(_cell(_p([_run(x[0], size=7.5, color=MUTED)], after=20) +
                                  _p([_run(x[1], bold=True, size=13, color=INK)], after=20) +
                                  _p([_run(x[2] or "", size=7.5, color=MUTED)], after=0), w[k], borders=edge)
                            for k, x in enumerate(b["items"]))
            return _tbl(["<w:tr>%s</w:tr>" % cells], w, cell_margin=(80, 80, 0, 120)) + _p([], after=80)
        if t == "table":
            return self.table(b, self.ex.get(i, 0))
        if t == "list":
            lid = self.new_list(bool(b.get("ordered")))
            small = b.get("small")
            return "".join(_p([_run(x, size=8 if small else 9.5, color=TEXT if small else INK)], num=lid, after=70, line=264)
                           for x in b["items"])
        if t == "quote":
            bar = '<w:left w:val="single" w:sz="18" w:space="8" w:color="%s"/>' % NAVY
            out = _p([_run(b["text"], size=9, color=INK)], border=bar, indent='w:left="200"', after=40, line=264)
            if b.get("cite"):
                out += _p([_run(b["cite"], size=7.5, color=MUTED)], border=bar, indent='w:left="200"', after=160)
            return out
        if t == "links":
            size = 8 if b.get("style") == "note" else 9
            out = ""
            for date, title, url in b["items"]:
                runs = [_run(date, size=size, color=MUTED), "<w:r><w:tab/></w:r>"] if date else []
                runs.append(self.link(title, url, size) if url else _run(title, size=size, color=INK))
                out += _p(runs, after=80, tabs=[("left", 1900)] if date else None,
                          indent='w:left="1900" w:hanging="1900"' if date else None)
            return out
        return ""

    def numbering(self):
        def abstract(i, ordered):
            if ordered:
                lvl = ('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
                       '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr>'
                       '<w:rPr><w:b/><w:color w:val="' + NAVY + '"/></w:rPr></w:lvl>')
            else:
                lvl = ('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u25aa"/>'
                       '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="300" w:hanging="300"/></w:pPr>'
                       '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:color w:val="' + NAVY + '"/></w:rPr></w:lvl>')
            return '<w:abstractNum w:abstractNumId="%d"><w:multiLevelType w:val="singleLevel"/>%s</w:abstractNum>' % (i, lvl)
        nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>',
                '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>']
        for k, ordered in enumerate(self.lists):
            # each list its own instance, so every numbered list starts at 1
            nums.append('<w:num w:numId="%d"><w:abstractNumId w:val="%d"/><w:lvlOverride w:ilvl="0">'
                        '<w:startOverride w:val="1"/></w:lvlOverride></w:num>' % (k + 3, 1 if ordered else 0))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering %s>%s%s%s</w:numbering>'
                % (_W_NS, abstract(0, False), abstract(1, True), "".join(nums)))

    def build(self):
        d = self.doc
        body = self.masthead() + "".join(self.block(i, b) for i, b in enumerate(d["blocks"]))
        sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rHeader"/>'
                '<w:headerReference w:type="first" r:id="rHeaderFirst"/>'
                '<w:footerReference w:type="default" r:id="rFooter"/><w:footerReference w:type="first" r:id="rFooter"/>'
                '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1080" w:right="964" w:bottom="1080" w:left="964" '
                'w:header="520" w:footer="480" w:gutter="0"/><w:titlePg/></w:sectPr>')
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document %s><w:body>%s%s</w:body></w:document>'
                    % (_W_NS, body, sect))
        tab = [("right", _TEXT_W)]
        header = _p([_run("%s (%s)" % (d.get("company") or d["title"], d.get("ticker") or ""), bold=True, size=7.5, color=INK),
                     "<w:r><w:tab/></w:r>",
                     _run(u"%s  \u00b7  %s" % (d.get("kicker") or "Research Brief", d.get("date") or ""), size=7.5, color=MUTED)],
                    tabs=tab, after=0, border='<w:bottom w:val="single" w:sz="4" w:space="4" w:color="%s"/>' % RULE)

        def field(code):
            r = _rpr(size=7.5, color=MUTED)
            return ('<w:r>%s<w:fldChar w:fldCharType="begin"/></w:r><w:r>%s<w:instrText xml:space="preserve"> %s </w:instrText></w:r>'
                    '<w:r>%s<w:fldChar w:fldCharType="separate"/></w:r><w:r>%s<w:t>1</w:t></w:r><w:r>%s<w:fldChar w:fldCharType="end"/></w:r>'
                    % (r, r, code, r, r, r))
        footer = _p([_run(d.get("footer") or "Plover Analytics", size=7.5, color=MUTED), "<w:r><w:tab/></w:r>",
                     _run("Page ", size=7.5, color=MUTED), field("PAGE"), _run(" of ", size=7.5, color=MUTED), field("NUMPAGES")],
                    tabs=tab, after=0, border='<w:top w:val="single" w:sz="4" w:space="4" w:color="%s"/>' % RULE)

        def part(tag, inner):
            return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:%s %s>%s</w:%s>' % (tag, _W_NS, inner, tag)

        styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles %s><w:docDefaults><w:rPrDefault><w:rPr>'
                  '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s" w:eastAsia="%s"/><w:color w:val="%s"/>'
                  '<w:sz w:val="19"/><w:szCs w:val="19"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
                  '<w:spacing w:after="100" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
                  '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                  '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
                  '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
                  '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
                  '</w:styles>' % (_W_NS, _FONT, _FONT, _FONT, _FONT, INK))
        fonts = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:fonts %s>'
                 '<w:font w:name="%s"><w:altName w:val="Arial"/><w:charset w:val="00"/><w:family w:val="swiss"/>'
                 '<w:pitch w:val="variable"/><w:embedRegular r:id="rF1" w:fontKey="%s"/>'
                 '<w:embedBold r:id="rF2" w:fontKey="%s"/></w:font>'
                 '<w:font w:name="Arial"><w:charset w:val="00"/><w:family w:val="swiss"/><w:pitch w:val="variable"/></w:font>'
                 '</w:fonts>' % (_W_NS, _FONT, _FONT_KEYS[0], _FONT_KEYS[1]))
        settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings %s><w:embedTrueTypeFonts/>'
                    '<w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/>'
                    '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
                    '</w:compat></w:settings>' % _W_NS)
        R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        rels = ['<Relationship Id="rStyles" Type="%sstyles" Target="styles.xml"/>' % R,
                '<Relationship Id="rSettings" Type="%ssettings" Target="settings.xml"/>' % R,
                '<Relationship Id="rFonts" Type="%sfontTable" Target="fontTable.xml"/>' % R,
                '<Relationship Id="rNumbering" Type="%snumbering" Target="numbering.xml"/>' % R,
                '<Relationship Id="rHeader" Type="%sheader" Target="header1.xml"/>' % R,
                '<Relationship Id="rHeaderFirst" Type="%sheader" Target="header2.xml"/>' % R,
                '<Relationship Id="rFooter" Type="%sfooter" Target="footer1.xml"/>' % R,
                '<Relationship Id="rLogo" Type="%simage" Target="media/plover-mark.png"/>' % R]
        rels += ['<Relationship Id="rL%d" Type="%shyperlink" Target="%s" TargetMode="External"/>' % (i, R, _x(u))
                 for i, u in enumerate(self.links, 1)]
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."
        files = {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                '<Default Extension="odttf" ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/>'
                '<Override PartName="/word/document.xml" ContentType="%sdocument.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="%sstyles+xml"/>'
                '<Override PartName="/word/settings.xml" ContentType="%ssettings+xml"/>'
                '<Override PartName="/word/fontTable.xml" ContentType="%sfontTable+xml"/>'
                '<Override PartName="/word/numbering.xml" ContentType="%snumbering+xml"/>'
                '<Override PartName="/word/header1.xml" ContentType="%sheader+xml"/>'
                '<Override PartName="/word/header2.xml" ContentType="%sheader+xml"/>'
                '<Override PartName="/word/footer1.xml" ContentType="%sfooter+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '</Types>' % ((CT,) * 8)),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="%sofficeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '</Relationships>' % R),
            "docProps/core.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties '
                'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title><dc:creator>Plover Analytics</dc:creator>'
                '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created></cp:coreProperties>' % (_x(d["title"]), now)),
            "word/document.xml": document,
            "word/styles.xml": styles,
            "word/settings.xml": settings,
            "word/fontTable.xml": fonts,
            "word/numbering.xml": self.numbering(),
            "word/header1.xml": part("hdr", header),
            "word/header2.xml": part("hdr", _p([])),
            "word/footer1.xml": part("ftr", footer),
            "word/media/plover-mark.png": (ASSETS / "plover-mark.png").read_bytes(),
            "word/fonts/font1.odttf": _obfuscate((ASSETS / "IBMPlexSans-400.ttf").read_bytes(), _FONT_KEYS[0]),
            "word/fonts/font2.odttf": _obfuscate((ASSETS / "IBMPlexSans-600.ttf").read_bytes(), _FONT_KEYS[1]),
            "word/_rels/fontTable.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rF1" Type="%sfont" Target="fonts/font1.odttf"/>'
                '<Relationship Id="rF2" Type="%sfont" Target="fonts/font2.odttf"/></Relationships>' % (R, R)),
            "word/_rels/document.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '%s</Relationships>' % "".join(rels)),
        }
        return _zip(files)


def to_docx(doc):
    return _Word(doc).build()


# ============================================================ Excel (.xlsx)
# cell styles: their index in cellXfs below
_XF = {None: 0, "header": 1, "#,##0": 2, "0.0": 3, "0.00": 4, "wrap": 5, "title": 6, "note": 7, "date": 8,
       "kicker": 9, "section": 10, "label": 11, "value": 12, "lead": 13}


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet(rows, widths, freeze=None):
    """rows: [[(value, style)]]; a str value is inline text, a number is a number, None an empty cell."""
    out = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, cell in enumerate(row):
            value, style = cell if isinstance(cell, tuple) else (cell, None)
            ref = "%s%d" % (_col(c), r)
            s = _XF.get(style, 0)
            if value is None:
                if s:
                    cells.append('<c r="%s" s="%d"/>' % (ref, s))
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append('<c r="%s" s="%d"><v>%r</v></c>' % (ref, s, float(value)))
            else:
                cells.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                             % (ref, s, _x(value)))
        out.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    cols = "".join('<col min="%d" max="%d" width="%.1f" customWidth="1"/>' % (i + 1, i + 1, w)
                   for i, w in enumerate(widths))
    pane = ('<pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>' % (freeze, freeze + 1)
            if freeze else "")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0" '
            'showGridLines="0">%s</sheetView></sheetViews>%s<sheetData>%s</sheetData></worksheet>'
            % (pane, "<cols>%s</cols>" % cols if cols else "", "".join(out)))


def to_xlsx(doc):
    """A Brief sheet (masthead, headline, Key Points, Key Data, the words), one sheet
    per exhibit with the numbers as numbers, and a Sources sheet."""
    ex = exhibits(doc)
    sheets, used = [], {"Brief", "Sources"}
    brief = [[(u"Plover Analytics  \u00b7  %s  \u00b7  %s" % (doc.get("kicker") or "Research Brief", doc.get("date") or ""), "kicker")],
             [(doc.get("company") or doc["title"], "title")], [(doc.get("line") or "", "note")],
             [(doc.get("subtitle") or "", "lead")], []]
    for i, b in enumerate(doc["blocks"]):
        t = b["t"]
        if t == "points":
            brief.append([("Key Points", "section")])
            brief += [[(u"\u25aa  " + x, "wrap")] for x in b["items"]]
            brief.append([])
        elif t == "keydata":
            brief.append([("Key Data", "section"), (None, "section")])
            brief += [[(k, "label"), (v, "value")] for k, v in b["items"]]
        elif t == "h":
            brief += [[], [(b["text"], "section"), (None, "section")]]
        elif t == "p":
            brief.append([(b["text"], {"note": "note", "sub": "label"}.get(b.get("style"), "wrap"))])
        elif t == "stats":
            brief += [[(it[0], "label"), (it[1], "value"), (it[2] or "", "note")] for it in b["items"]]
        elif t == "list":
            brief += [[((("%d. " % k) if b.get("ordered") else u"\u25aa  ") + item, "wrap")]
                      for k, item in enumerate(b["items"], 1)]
        elif t == "quote":
            brief.append([(u"\u201c%s\u201d" % b["text"], "wrap")])
            if b.get("cite"):
                brief.append([(b["cite"], "note")])
        elif t == "links":
            brief += [([(date, "note")] if date else []) + [(title, "wrap"), (url or "", "note")]
                      for date, title, url in b["items"]]
        elif t == "table":
            n = ex.get(i)
            name = _safe_name(("Ex %d %s" % (n, b.get("name") or "Table")) if n else (b.get("name") or "Table"))
            base, k = name, 2
            while name in used:
                name = _safe_name("%s %d" % (base[:27], k))
                k += 1
            used.add(name)
            brief.append([((u"Exhibit %d: %s \u2014 see the sheet \u201c%s\u201d." % (n, b.get("title"), name)) if n else
                           (u"See the sheet \u201c%s\u201d." % name), "note")])
            fm = b.get("formats") or [None] * len(b["cols"])
            units = b.get("units") or [None] * len(b["cols"])
            rows = [[((("Exhibit %d: " % n) if n else "") + (b.get("title") or b.get("name") or ""), "title")],
                    [(("Source: " + b["source"]) if b.get("source") else "", "note")], [],
                    [(c[0] + (" (%s)" % u if u else ""), "header") for c, u in zip(b["cols"], units)]]
            vals = b.get("values")
            for r_i, row in enumerate(b["rows"]):
                rows.append([(vals[r_i][j], fm[j]) if (fm[j] and vals is not None) else (text, None)
                             for j, text in enumerate(row)])
            if b.get("note"):
                rows += [[], [(b["note"], "note")]]
            sheets.append((name, _sheet(rows, [40] + [17] * (len(b["cols"]) - 1), freeze=4)))
    sources = [[("Sources", "section")]] + [[(s, "wrap")] for s in doc.get("sources") or []]
    if doc.get("calc"):
        sources += [[], [("How each figure is worked out", "section"), (None, "section")]] + \
                   [[(k, "label"), (v, "wrap")] for k, v in doc["calc"]]
    if doc.get("disclaimer"):
        sources += [[], [("Important information", "section"), (None, "section")], [(doc["disclaimer"], "wrap")]]
    sheets = [("Brief", _sheet(brief, [70, 18, 40]))] + sheets + [("Sources", _sheet(sources, [42, 110]))]

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2"><numFmt numFmtId="164" formatCode="d mmm yyyy"/><numFmt numFmtId="165" formatCode="0.0"/></numFmts>'
        '<fonts count="8">'
        '<font><sz val="10"/><color rgb="FF{ink}"/><name val="Arial"/></font>'
        '<font><b/><sz val="9"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>'
        '<font><b/><sz val="15"/><color rgb="FF{ink}"/><name val="Arial"/></font>'
        '<font><sz val="9"/><color rgb="FF{muted}"/><name val="Arial"/></font>'
        '<font><b/><sz val="8"/><color rgb="FF{navy}"/><name val="Arial"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF{navy}"/><name val="Arial"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF{ink}"/><name val="Arial"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF{navy}"/><name val="Arial"/></font>'
        '</fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF{navy}"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="3"><border/><border><bottom style="thin"><color rgb="FF{grid}"/></bottom></border>'
        '<border><bottom style="medium"><color rgb="FF{navy}"/></bottom></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="14">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment wrapText="1" vertical="bottom"/></xf>'
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '<xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="5" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="6" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right"/></xf>'
        '<xf numFmtId="0" fontId="7" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    ).format(ink=INK, muted=MUTED, navy=NAVY, grid=GRID)
    SS = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="%ssheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="%sstyles+xml"/>' % (SS, SS)
            + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="%sworksheet+xml"/>' % (i + 1, SS)
                      for i in range(len(sheets))) + '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%sofficeDocument" Target="xl/workbook.xml"/></Relationships>' % R),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>%s</sheets></workbook>'
            % "".join('<sheet name="%s" sheetId="%d" r:id="rS%d"/>' % (_x(n), i + 1, i + 1) for i, (n, _) in enumerate(sheets))),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join('<Relationship Id="rS%d" Type="%sworksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, R, i + 1)
                      for i in range(len(sheets)))
            + '<Relationship Id="rStyles" Type="%sstyles" Target="styles.xml"/></Relationships>' % R),
        "xl/styles.xml": styles,
    }
    for i, (_, xml) in enumerate(sheets):
        files["xl/worksheets/sheet%d.xml" % (i + 1)] = xml
    return _zip(files)


# ===================================================================== PDF
class _Font(object):
    """A TrueType font read just enough to embed it whole and lay out text:
    the character map, the advance widths and the metrics the PDF asks for."""

    def __init__(self, path):
        self.data = path.read_bytes()
        d = self.data
        n = struct.unpack(">H", d[4:6])[0]
        self.tables = {}
        for i in range(n):
            tag, _, off, length = struct.unpack(">4sIII", d[12 + 16 * i:28 + 16 * i])
            self.tables[tag.decode("latin-1")] = (off, length)
        head = self._t("head")
        self.upem = struct.unpack(">H", head[18:20])[0]
        self.bbox = struct.unpack(">hhhh", head[36:44])
        hhea = self._t("hhea")
        self.ascent, self.descent = struct.unpack(">hh", hhea[4:8])
        nh = struct.unpack(">H", hhea[34:36])[0]
        hmtx = self._t("hmtx")
        self.adv = [struct.unpack(">H", hmtx[4 * i:4 * i + 2])[0] for i in range(nh)]
        os2 = self._t("OS/2")
        self.cap = struct.unpack(">h", os2[88:90])[0] if len(os2) >= 90 else self.ascent
        self.cmap = self._cmap()

    def _t(self, tag):
        off, length = self.tables[tag]
        return self.data[off:off + length]

    def _cmap(self):
        t = self._t("cmap")
        n = struct.unpack(">H", t[2:4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", t[4 + 8 * i:12 + 8 * i])
            fmt = struct.unpack(">H", t[off:off + 2])[0]
            if pid in (0, 3) and fmt == 4 and best is None:
                best = off
        out = {}
        off = best
        segx2 = struct.unpack(">H", t[off + 6:off + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(">%dH" % seg, t[off + 14:off + 14 + segx2])
        starts = struct.unpack(">%dH" % seg, t[off + 16 + segx2:off + 16 + 2 * segx2])
        deltas = struct.unpack(">%dh" % seg, t[off + 16 + 2 * segx2:off + 16 + 3 * segx2])
        ro_at = off + 16 + 3 * segx2
        ranges = struct.unpack(">%dH" % seg, t[ro_at:ro_at + segx2])
        for i in range(seg):
            for ch in range(starts[i], ends[i] + 1):
                if ch == 0xFFFF:
                    continue
                if ranges[i] == 0:
                    g = (ch + deltas[i]) & 0xFFFF
                else:
                    at = ro_at + 2 * i + ranges[i] + 2 * (ch - starts[i])
                    g = struct.unpack(">H", t[at:at + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                if g:
                    out[ch] = g
        return out

    def glyph(self, ch):
        return self.cmap.get(ord(ch)) or self.cmap.get(ord(_FALLBACK.get(ch, "?")), 0)

    def width(self, text, size):
        total = 0
        for ch in text:
            g = self.glyph(ch)
            total += self.adv[g] if g < len(self.adv) else self.adv[-1]
        return total * size / self.upem


# characters the Latin font lacks, drawn as the nearest one it has
_FALLBACK = {u"\u2265": ">", u"\u2264": "<", u"\u2192": ">", u"\u00a0": " ", u"\u2011": "-", u"\u2010": "-",
             u"\u202f": " ", u"\u2009": " ", u"\u2002": " ", u"\u2003": " "}

_A4 = (595.28, 841.89)
_M = 48.0              # side margins, points
_TOP, _BOTTOM = 62.0, 58.0


def _rgb(hexcolor):
    return "%.3f %.3f %.3f" % tuple(int(hexcolor[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _circle(cx, cy, r, op):
    m = r * 0.5523
    return ("%.2f %.2f m %.2f %.2f %.2f %.2f %.2f %.2f c %.2f %.2f %.2f %.2f %.2f %.2f c "
            "%.2f %.2f %.2f %.2f %.2f %.2f c %.2f %.2f %.2f %.2f %.2f %.2f c %s" % (
                cx + r, cy, cx + r, cy + m, cx + m, cy + r, cx, cy + r, cx - m, cy + r, cx - r, cy + m, cx - r, cy,
                cx - r, cy - m, cx - m, cy - r, cx, cy - r, cx + m, cy - r, cx + r, cy - m, cx + r, cy, op))


class _Pdf(object):
    W = _A4[0] - 2 * _M

    def __init__(self, doc):
        self.doc = doc
        self.reg = _Font(FONTS / "IBMPlexSans-400.ttf")
        self.bold = _Font(FONTS / "IBMPlexSans-600.ttf")
        self.used = {id(self.reg): {}, id(self.bold): {}}
        self.pages = []
        self.new_page()

    # ---- pages and text
    def new_page(self):
        self.ops, self.page_links = [], []
        self.pages.append((self.ops, self.page_links))
        self.y = _A4[1] - (_M if len(self.pages) == 1 else _TOP)

    def need(self, h):
        if self.y - h < _BOTTOM:
            self.new_page()
            return True
        return False

    def text(self, x, y, s, font, size, color=INK):
        s = (s or "").replace(u"\u00ad", "")
        if not s:
            return
        used = self.used[id(font)]
        hexes = []
        for ch in s:
            g = font.glyph(ch)
            used[g] = _FALLBACK.get(ch, ch) or " "
            hexes.append("%04X" % g)
        name = "F2" if font is self.bold else "F1"
        self.ops.append("BT /%s %.2f Tf %s rg %.2f %.2f Td <%s> Tj ET" % (name, size, _rgb(color), x, y, "".join(hexes)))

    def text_right(self, x_right, y, s, font, size, color=INK):
        self.text(x_right - font.width(s, size), y, s, font, size, color)

    def rule(self, x0, x1, y, color=RULE, w=0.6):
        self.ops.append("%s RG %.2f w %.2f %.2f m %.2f %.2f l S" % (_rgb(color), w, x0, y, x1, y))

    def fill(self, x, y, w, h, color):
        self.ops.append("%s rg %.2f %.2f %.2f %.2f re f" % (_rgb(color), x, y, w, h))

    def logo(self, x, y, size, color=NAVY):
        """The Plover mark (web/assets/logo-mark.svg), drawn as lines on its 128-unit square."""
        k = size / 128.0

        def P(px, py):
            return x + px * k, y + (128 - py) * k
        c = _rgb(color)
        o = ["q %s RG %s rg %.2f w 1 J 1 j" % (c, c, 8 * k)]
        cx, cy = P(64, 64)
        o.append(_circle(cx, cy, 54 * k, "S"))
        pts = [P(22, 86), P(46, 56), P(62, 70), P(86, 44), P(106, 58)]
        o.append("%.2f %.2f m " % pts[0] + " ".join("%.2f %.2f l" % p for p in pts[1:]) + " S")
        for px, py in ((46, 56), (86, 44)):
            dx, dy = P(px, py)
            o.append(_circle(dx, dy, 8 * k, "f"))
        o.append("%s RG %.2f w" % (_rgb("8CA6C8"), 7 * k))
        for a, b2 in ((30, 62), (74, 98)):
            p0, p1 = P(a, 100), P(b2, 100)
            o.append("%.2f %.2f m %.2f %.2f l S" % (p0[0], p0[1], p1[0], p1[1]))
        o.append("Q")
        self.ops.append(" ".join(o))

    def wrap(self, s, font, size, width):
        lines = []
        for para in (s or "").split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                trial = (line + " " + w) if line else w
                if font.width(trial, size) <= width or not line:
                    if not line and font.width(w, size) > width:
                        cut = w
                        while cut and font.width(cut, size) > width:
                            k = len(cut) - 1
                            while k > 1 and font.width(cut[:k], size) > width:
                                k -= 1
                            lines.append(cut[:k])
                            cut = cut[k:]
                        line = cut
                    else:
                        line = trial
                else:
                    lines.append(line)
                    line = w
            lines.append(line)
        return lines

    def para(self, s, font, size, color=INK, lead=1.45, width=None, x=_M, after=6, link=None, bar=None):
        width = width or self.W
        for line in self.wrap(s, font, size, width):
            self.need(size * lead)
            self.y -= size * lead
            if bar is not None:
                self.fill(bar, self.y - size * 0.3, 1.8, size * lead, NAVY)
            self.text(x, self.y + size * 0.25, line, font, size, color)
            if link:
                self.page_links.append((x, self.y, x + font.width(line, size), self.y + size * lead, link))
        self.y -= after

    # ---- the first page
    def masthead(self):
        d = self.doc
        top = self.y
        self.logo(_M, top - 22, 22)
        self.text(_M + 29, top - 15, "Plover Analytics", self.bold, 11.5, INK)
        self.text_right(_A4[0] - _M, top - 9, (d.get("kicker") or "Research Brief").upper(), self.bold, 7, NAVY)
        self.text_right(_A4[0] - _M, top - 20, d.get("date") or "", self.reg, 7.5, MUTED)
        self.y = top - 30
        self.fill(_M, self.y, self.W, 1.6, NAVY)
        self.y -= 8
        self.para(d.get("company") or d["title"], self.bold, 21, INK, lead=1.3, after=2)
        if d.get("line"):
            self.para(d["line"], self.reg, 8.5, MUTED, after=8)
        if d.get("subtitle"):
            self.para(d["subtitle"], self.bold, 11.5, NAVY, lead=1.35, after=14)

    def points_and_data(self, points, data):
        lw = self.W * 0.58
        rx, rw = _M + lw + 18, self.W - lw - 18
        rows_h = 15.0
        ph = 26 + rows_h * len(data) + 8
        self.need(ph)
        top = self.y
        self.fill(rx, top - ph, rw, ph, PANEL)
        self.text(rx + 10, top - 16, "KEY DATA", self.bold, 7, NAVY)
        y = top - 26
        for k, v in data:
            y -= rows_h
            self.text(rx + 10, y + 4.5, k, self.reg, 7.5, TEXT)
            self.text_right(rx + rw - 10, y + 4.5, v, self.bold, 8, INK)
            self.rule(rx + 10, rx + rw - 10, y, GRID, 0.5)
        self.text(_M, top - 16, "KEY POINTS", self.bold, 7, NAVY)
        self.y = top - 22
        for p in points:
            for k, line in enumerate(self.wrap(p, self.reg, 9, lw - 14)):
                self.y -= 13.2
                if not k:
                    self.fill(_M + 1, self.y + 4.2, 3.6, 3.6, NAVY)
                self.text(_M + 13, self.y + 2.5, line, self.reg, 9, INK)
            self.y -= 5
        self.y = min(self.y, top - ph) - 10

    # ---- the blocks
    def heading(self, s):
        self.need(100)       # a heading never ends a page: room for it and the first lines under it
        self.y -= 24
        self.text(_M, self.y, s, self.bold, 10.5, NAVY)
        self.y -= 6
        self.rule(_M, _A4[0] - _M, self.y, NAVY, 0.8)
        self.y -= 10

    def stats(self, items):
        width = self.W / max(1, len(items))
        self.need(56)
        top = self.y
        self.rule(_M, _A4[0] - _M, top, GRID)
        for i, (label, value, small) in enumerate(items):
            x = _M + i * width + (8 if i else 0)
            self.text(x, top - 14, label, self.reg, 7.5, MUTED)
            self.text(x, top - 32, value, self.bold, 14, INK)
            for k, line in enumerate(self.wrap(small or "", self.reg, 7, width - 12)[:2]):
                self.text(x, top - 43 - 8.5 * k, line, self.reg, 7, MUTED)
            if i:
                self.ops.append("%s RG 0.6 w %.2f %.2f m %.2f %.2f l S" % (
                    _rgb(GRID), _M + i * width, top - 4, _M + i * width, top - 56))
        self.y = top - 62
        self.rule(_M, _A4[0] - _M, self.y, GRID)
        self.y -= 6

    def table(self, b, number):
        cols, rows = b["cols"], b["rows"]
        strong, muted = set(b.get("strong") or ()), set(b.get("muted") or ())
        avail = self.W
        size = 8.0 if len(cols) <= 6 else 7.3
        hs = size - 0.6
        pad = 7.0
        # a header wraps between its words, never inside one; a number never wraps
        nat = [max([max(self.bold.width(w, hs) for w in c[0].split())] +
                   [self.reg.width(r[i], size) for r in rows] +
                   [self.bold.width(r[i], size) for n, r in enumerate(rows) if n in strong]) + pad + 2
               for i, c in enumerate(cols)]
        rest = sum(nat[1:])
        if sum(nat) <= avail:
            # room to spare: every column widens in proportion, so no one column takes it all
            widths = [w * avail / sum(nat) for w in nat]
        else:
            # too wide: the numbers keep their width and the first (text) column wraps
            widths = [max(avail - rest, min(nat[0], avail * 0.42))] + nat[1:]
            if sum(widths) > avail:
                scale = (avail - widths[0]) / rest if rest else 1
                widths = [widths[0]] + [w * scale for w in nat[1:]]
        xs = [_M]
        for w in widths[:-1]:
            xs.append(xs[-1] + w)

        if b.get("title"):
            self.need(64)
            self.y -= 14
            cap = "Exhibit %d: " % number
            cw = self.bold.width(cap, 8)
            self.text(_M, self.y, cap, self.bold, 8, NAVY)
            for k, line in enumerate(self.wrap(b["title"], self.bold, 8, self.W - cw)):
                if k:
                    self.y -= 11
                self.text(_M + cw, self.y, line, self.bold, 8, INK)
            self.y -= 7

        def header():
            lines = [self.wrap(c[0], self.bold, hs, widths[i] - pad) for i, c in enumerate(cols)]
            h = max(len(l) for l in lines) * (hs + 2) + 8
            self.fill(_M, self.y - h, avail, h, NAVY)
            for i, c in enumerate(cols):
                n = len(lines[i])
                for k, line in enumerate(lines[i]):
                    y = self.y - h + 5 + (n - 1 - k) * (hs + 2)
                    if c[1] == "r":
                        self.text_right(xs[i] + widths[i] - pad / 2, y, line, self.bold, hs, "FFFFFF")
                    else:
                        self.text(xs[i] + pad / 2, y, line, self.bold, hs, "FFFFFF")
            self.y -= h

        self.need(46)
        header()
        for n, row in enumerate(rows):
            font = self.bold if n in strong else self.reg
            cells = [self.wrap(row[i], font, size, widths[i] - pad) if cols[i][1] == "l" else [row[i]]
                     for i in range(len(cols))]
            h = max(len(c) for c in cells) * (size + 2.6) + 6
            if self.need(h):
                header()
            shade = ME if n in strong else (ZEBRA if n % 2 else None)
            if shade:
                self.fill(_M, self.y - h, avail, h, shade)
            color = MUTED if n in muted else INK
            for i in range(len(cols)):
                for k, line in enumerate(cells[i]):
                    y = self.y - 3.5 - (size + 2.6) * (k + 1) + 2
                    if cols[i][1] == "r":
                        self.text_right(xs[i] + widths[i] - pad / 2, y, line, font, size, color)
                    else:
                        self.text(xs[i] + pad / 2, y, line, font, size, color)
            self.y -= h
            self.rule(_M, _A4[0] - _M, self.y, GRID, 0.5)
        self.rule(_M, _A4[0] - _M, self.y, NAVY, 0.9)
        self.y -= 4
        notes = [x for x in (("Source: " + b["source"]) if b.get("source") else None, b.get("note")) if x]
        if notes:
            self.para(" ".join(notes), self.reg, 7, MUTED, lead=1.4, after=8)
        else:
            self.y -= 8

    def blocks(self):
        d = self.doc
        ex = exhibits(d)
        pts = next((x["items"] for x in d["blocks"] if x["t"] == "points"), [])
        for i, b in enumerate(d["blocks"]):
            t = b["t"]
            if t == "keydata":
                self.points_and_data(pts, b["items"])
            elif t == "h":
                if b.get("about"):
                    self.new_page()
                    self.y += 24          # no space above the heading at the top of a page
                self.heading(b["text"])
            elif t == "p":
                st = b.get("style") or "body"
                if st == "lead":
                    self.para(b["text"], self.bold, 11, INK, lead=1.35)
                elif st == "note":
                    self.para(b["text"], self.reg, 7.5, MUTED, lead=1.45, after=5)
                elif st == "sub":
                    self.need(40)
                    self.y -= 5
                    self.para(b["text"], self.bold, 9, INK, lead=1.4, after=3)
                else:
                    self.para(b["text"], self.reg, 9, INK, lead=1.55, after=6)
            elif t == "stats":
                self.stats(b["items"])
            elif t == "table":
                self.table(b, ex.get(i, 0))
            elif t == "list":
                small = b.get("small")
                size = 7.5 if small else 9
                for k, item in enumerate(b["items"], 1):
                    for j, line in enumerate(self.wrap(item, self.reg, size, self.W - 16)):
                        self.need(size * 1.5)
                        self.y -= size * 1.5
                        if not j:
                            if b.get("ordered"):
                                self.text(_M, self.y + 2.6, "%d." % k, self.bold, size, NAVY)
                            else:
                                self.fill(_M + 1, self.y + size * 0.42, 3.2, 3.2, NAVY)
                        self.text(_M + 16, self.y + 2.6, line, self.reg, size, TEXT if small else INK)
                    self.y -= 2.5
                self.y -= 3
            elif t == "quote":
                self.para(b["text"], self.reg, 8.5, INK, lead=1.5, x=_M + 12, width=self.W - 12, after=1, bar=_M)
                if b.get("cite"):
                    self.para(b["cite"], self.reg, 7, MUTED, x=_M + 12, width=self.W - 12, after=9, bar=_M)
            elif t == "links":
                for date, title, url in b["items"]:
                    self.need(26)
                    indent = 86 if date else 0
                    size = 7.5 if b.get("style") == "note" else 8.5
                    if date:
                        self.text(_M, self.y - size * 1.45 + size * 0.25, date, self.reg, size, MUTED)
                    self.para(title, self.reg, size, NAVY if url else INK, x=_M + indent, width=self.W - indent,
                              lead=1.45, after=3, link=url)

    # ---- the file
    def _font_objects(self, font, first):
        """Five objects: Type0 font, CIDFont, descriptor, font file, ToUnicode."""
        used = self.used[id(font)]
        scale = 1000.0 / font.upem
        ws = " ".join("%d [%d]" % (g, round(font.adv[g if g < len(font.adv) else -1] * scale)) for g in sorted(used))
        name = "IBMPlexSans-SemiBold" if font is self.bold else "IBMPlexSans"
        cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) "
                "/Ordering (UCS) /Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def "
                "1 begincodespacerange <0000> <FFFF> endcodespacerange\n")
        items = sorted(used.items())
        for i in range(0, len(items), 100):
            chunk = items[i:i + 100]
            cmap += "%d beginbfchar\n" % len(chunk) + "".join(
                "<%04X> <%s>\n" % (g, "".join("%04X" % ord(c) for c in (ch or " "))) for g, ch in chunk) + "endbfchar\n"
        cmap += "endcmap CMapName currentdict /CMap defineresource pop end end"
        b = [round(v * scale) for v in font.bbox]
        f0, f1, f2, f3, f4 = first, first + 1, first + 2, first + 3, first + 4
        return [
            (f0, "<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
             % (name, f1, f4)),
            (f1, "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
                 "/Supplement 0 >> /FontDescriptor %d 0 R /CIDToGIDMap /Identity /W [%s] >>" % (name, f2, ws)),
            (f2, "<< /Type /FontDescriptor /FontName /%s /Flags 32 /FontBBox [%d %d %d %d] /ItalicAngle 0 /Ascent %d "
                 "/Descent %d /CapHeight %d /StemV %d /FontFile2 %d 0 R >>"
             % (name, b[0], b[1], b[2], b[3], round(font.ascent * scale), round(font.descent * scale),
                round(font.cap * scale), 120 if font is self.bold else 80, f3)),
            (f3, ("stream", font.data, {"Length1": len(font.data)})),
            (f4, ("stream", cmap.encode("ascii"), {})),
        ]

    def output(self):
        d = self.doc
        total = len(self.pages)
        head_l = "%s (%s)" % (d.get("company") or d["title"], d.get("ticker") or "")
        head_r = u"%s  \u00b7  %s" % (d.get("kicker") or "Research Brief", d.get("date") or "")
        for i, (ops, _) in enumerate(self.pages, 1):
            self.ops = ops
            if i > 1:
                y = _A4[1] - 34
                self.logo(_M, y - 2.5, 10)
                self.text(_M + 14, y, head_l, self.bold, 7.5, INK)
                self.text_right(_A4[0] - _M, y, head_r, self.reg, 7.5, MUTED)
                self.rule(_M, _A4[0] - _M, y - 7, RULE, 0.6)
            self.rule(_M, _A4[0] - _M, 40, RULE, 0.6)
            self.text(_M, 28, d.get("footer") or "Plover Analytics", self.reg, 7, MUTED)
            self.text_right(_A4[0] - _M, 28, "Page %d of %d" % (i, total), self.reg, 7, MUTED)
        objs = {}
        f1, f2 = 4, 9
        nxt = 14
        page_ids = []
        for ops, links in self.pages:
            pid, cid = nxt, nxt + 1
            nxt += 2
            annots = []
            for (x0, y0, x1, y1, url) in links:
                objs[nxt] = ("<< /Type /Annot /Subtype /Link /Rect [%.2f %.2f %.2f %.2f] /Border [0 0 0] "
                             "/A << /S /URI /URI (%s) >> >>" % (x0, y0, x1, y1, _pdf_str(url)))
                annots.append("%d 0 R" % nxt)
                nxt += 1
            objs[pid] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 %d 0 R "
                         "/F2 %d 0 R >> >> /Contents %d 0 R%s >>" % (_A4[0], _A4[1], f1, f2, cid,
                                                                    (" /Annots [%s]" % " ".join(annots)) if annots else ""))
            objs[cid] = ("stream", "\n".join(ops).encode("latin-1"), {})
            page_ids.append(pid)
        for oid, body in self._font_objects(self.reg, f1) + self._font_objects(self.bold, f2):
            objs[oid] = body
        objs[1] = "<< /Type /Catalog /Pages 2 0 R >>"
        objs[2] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % p for p in page_ids), len(page_ids))
        objs[3] = "<< /Title %s /Author (Plover Analytics) /Producer (Plover Analytics) /CreationDate (D:%s) >>" % (
            _pdf_text(d["title"]), datetime.datetime.utcnow().strftime("%Y%m%d%H%M%SZ"))
        out = io.BytesIO()
        out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for oid in sorted(objs):
            offsets[oid] = out.tell()
            body = objs[oid]
            out.write(b"%d 0 obj\n" % oid)
            if isinstance(body, tuple):
                _, raw, extra = body
                data = zlib.compress(raw)
                dic = "<< /Length %d /Filter /FlateDecode%s >>" % (
                    len(data), "".join(" /%s %d" % kv for kv in extra.items()))
                out.write(dic.encode("ascii") + b"\nstream\n" + data + b"\nendstream")
            else:
                out.write(body.encode("latin-1"))
            out.write(b"\nendobj\n")
        xref = out.tell()
        n = max(objs) + 1
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % n)
        for oid in range(1, n):
            out.write(b"%010d 00000 n \n" % offsets[oid] if oid in offsets else b"0000000000 65535 f \n")
        out.write(b"trailer\n<< /Size %d /Root 1 0 R /Info 3 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref))
        return out.getvalue()


def _pdf_str(s):
    s = "".join(ch if 32 <= ord(ch) < 127 else "%%%02X" % ord(ch) for ch in s)
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_text(s):
    """A PDF text string in UTF-16, for the document title."""
    return "<FEFF%s>" % "".join("%04X" % c for c in struct.unpack(">%dH" % len(s), s.encode("utf-16-be")))


def to_pdf(doc):
    p = _Pdf(doc)
    p.masthead()
    p.blocks()
    return p.output()


RENDER = {"pdf": to_pdf, "docx": to_docx, "xlsx": to_xlsx}
MEDIA = {"pdf": "application/pdf",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
