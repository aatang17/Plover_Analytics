// Render draft.md to out/amd-lisa-su.docx. The draft is the content; this only lays it out.
// Run: NODE_PATH=<dir with docx installed>/node_modules node render_docx.js
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
  AlignmentType, ImageRun, ShadingType, BorderStyle, LevelFormat, PageBreak, Footer, PageNumber,
} = require("docx");

const HERE = __dirname;
const md = fs.readFileSync(path.join(HERE, "draft.md"), "utf8").split("\n");
const INK = "0F172A", MUTED = "64748B", RULE = "E2E8F0", NAVY = "1A4D8F";
const FONT = "Arial";
const PAGE_W = 11906, MARGIN = 1134, CONTENT_W = PAGE_W - 2 * MARGIN; // A4, 2 cm margins

// **bold**, *italic*, `code` -> runs
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: "Consolas", size: 17, ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), italics: true, ...base }));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}

function table(lines) {
  const rows = lines.filter((l, i) => i !== 1).map((l) => l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
  const n = rows[0].length;
  const first = Math.round(CONTENT_W * (n > 4 ? 0.28 : 0.4));
  const rest = Math.floor((CONTENT_W - first) / (n - 1));
  const widths = [first].concat(Array(n - 1).fill(rest));
  widths[0] = CONTENT_W - rest * (n - 1);
  const border = { style: BorderStyle.SINGLE, size: 4, color: RULE };
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((r, ri) => new TableRow({
      tableHeader: ri === 0,
      children: r.map((c, ci) => new TableCell({
        width: { size: widths[ci], type: WidthType.DXA },
        shading: ri === 0 ? { type: ShadingType.CLEAR, fill: "F1F5F9", color: "auto" } : undefined,
        borders: { top: none, left: none, right: none, bottom: border },
        margins: { top: 60, bottom: 60, left: 80, right: 80 },
        children: [new Paragraph({
          alignment: ci === 0 ? AlignmentType.LEFT : AlignmentType.RIGHT,
          children: runs(c, { size: 18, bold: ri === 0, color: ri === 0 ? MUTED : INK }),
        })],
      })),
    })),
  });
}

function image(rel) {
  const file = path.join(HERE, "out", rel);
  const buf = fs.readFileSync(file);
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20); // PNG IHDR
  const width = 620, height = Math.round((620 * h) / w);
  return new Paragraph({ spacing: { before: 160, after: 200 },
    children: [new ImageRun({ type: "png", data: buf, transformation: { width, height },
      altText: { title: rel, description: "Chart " + rel, name: rel } })] });
}

const body = [];
let title = "", meta = [];
for (let i = 0; i < md.length; i++) {
  const l = md[i];
  if (l.startsWith("# ")) { title = l.slice(2); continue; }
  if (!title || (meta.length < 2 && l.startsWith("*") && !body.length)) { if (l.trim()) meta.push(l.replace(/^\*|\*$/g, "")); continue; }
  if (!l.trim()) continue;
  if (l.startsWith("## ")) {
    const h = l.slice(3);
    if (h.startsWith("Appendix")) body.push(new Paragraph({ children: [new PageBreak()] }));
    body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(h)] }));
  } else if (l.startsWith("|")) {
    const block = [];
    while (i < md.length && md[i].startsWith("|")) block.push(md[i++]);
    i--;
    body.push(table(block));
  } else if (l.startsWith("![")) {
    body.push(image(l.match(/\(([^)]+)\)/)[1]));
  } else if (l.startsWith("- ")) {
    body.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: runs(l.slice(2)) }));
  } else if (l.startsWith("*") && l.endsWith("*") && !l.startsWith("**")) {
    body.push(new Paragraph({ spacing: { before: 60, after: 200 }, children: runs(l.slice(1, -1), { size: 16, color: MUTED }) }));
  } else {
    body.push(new Paragraph({ spacing: { after: 160, line: 290 }, children: runs(l) }));
  }
}

const cover = [
  new Paragraph({ spacing: { before: 2400, after: 120 }, children: [new TextRun({ text: "PLOVER ANALYTICS · COMPANY NOTE", size: 18, color: NAVY, bold: true, characterSpacing: 40 })] }),
  new Paragraph({ spacing: { after: 360 }, children: [new TextRun({ text: title, size: 44, bold: true, color: INK })] }),
  new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 8 } }, spacing: { after: 240 }, children: [] }),
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: "Advanced Micro Devices, Inc. (AMD) · Intel · NVIDIA", size: 20, color: INK })] }),
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: "Asia Economics Observations · 30 September 2026", size: 20, color: MUTED })] }),
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: "Data as of 30 September 2026: SEC filings via Plover Analytics", size: 20, color: MUTED })] }),
  new Paragraph({ spacing: { before: 2400 }, children: [new TextRun({ text: "All company figures as filed with the SEC. Calculated ratios are marked in the text; formulas are in the appendix.", size: 16, color: MUTED, italics: true })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

const doc = new Document({
  creator: "Plover Analytics",
  title,
  styles: {
    default: { document: { run: { font: FONT, size: 20, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: NAVY, font: FONT },
        paragraph: { spacing: { before: 320, after: 140 }, outlineLevel: 0, keepNext: true } },
    ],
  },
  numbering: { config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 360, hanging: 240 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      children: [new TextRun({ text: "Plover Analytics · AMD under Lisa Su · ", size: 14, color: MUTED }),
                 new TextRun({ children: [PageNumber.CURRENT], size: 14, color: MUTED })] })] }) },
    children: cover.concat(body),
  }],
});

Packer.toBuffer(doc).then((b) => {
  fs.writeFileSync(path.join(HERE, "out", "amd-lisa-su.docx"), b);
  console.log("out/amd-lisa-su.docx", b.length, "bytes");
});
