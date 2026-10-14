"""Check and repair the native text layer of a PDF before it is parsed. No OCR, no network.

A page can have text that is still wrong, and step 1 of the OCR pipeline only looked at whether text exists:
- Identity-H TrueType fonts without /ToUnicode (6 FDA PDFs): MuPDF reports glyph ids, so letters come out
  shifted ("and" -> "DQG") and digits fall into control characters that are dropped without a trace.
  Repaired by adding a /ToUnicode CMap built from the same-named reference font, after checking that every
  glyph the PDF uses has the same outline and advance width in both fonts. The repaired copy goes to
  data/repaired/; the original in data/raw/ is not touched.
- Symbol, Wingdings and Webdings fonts whose /ToUnicode points into the private use area (U+F0xx):
  mapped to the real characters per font (>= instead of U+F0B3).
Whatever is not repaired is reported per page, and those pages are read whole by the OCR pipeline.
"""
from __future__ import annotations
import hashlib
import io
from pathlib import Path
import fitz
from fontTools.ttLib import TTFont

VERSION = "text-layer-1"
FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts")]
REFERENCE_FONTS = {
    "ArialMT": "arial.ttf", "Arial-BoldMT": "arialbd.ttf", "Arial-ItalicMT": "ariali.ttf", "Arial-BoldItalicMT": "arialbi.ttf",
    "TimesNewRomanPSMT": "times.ttf", "TimesNewRomanPS-BoldMT": "timesbd.ttf", "TimesNewRomanPS-ItalicMT": "timesi.ttf",
    "TimesNewRomanPS-BoldItalicMT": "timesbi.ttf", "SymbolMT": "symbol.ttf", "Wingdings-Regular": "wingding.ttf",
    # Word also writes them under family names; the glyph check guards against a wrong match.
    "Arial": "arial.ttf", "Arial,Bold": "arialbd.ttf", "Arial,Italic": "ariali.ttf", "Arial,BoldItalic": "arialbi.ttf",
    "TimesNewRoman": "times.ttf", "TimesNewRoman,Bold": "timesbd.ttf", "TimesNewRoman,Italic": "timesi.ttf", "TimesNewRoman,BoldItalic": "timesbi.ttf",
}

# Adobe Symbol encoding (code -> character). Codes with no Unicode equivalent (bracket pieces) are left out.
SYMBOL = dict(zip(range(0x20, 0x7F), " !\u2200#\u2203%&\u220b()\u2217+,\u2212./0123456789:;<=>?\u2245"
                  "\u0391\u0392\u03a7\u0394\u0395\u03a6\u0393\u0397\u0399\u03d1\u039a\u039b\u039c\u039d\u039f\u03a0\u0398\u03a1\u03a3\u03a4\u03a5\u03c2\u03a9\u039e\u03a8\u0396"
                  "[\u2234]\u22a5_\uf8e5\u03b1\u03b2\u03c7\u03b4\u03b5\u03c6\u03b3\u03b7\u03b9\u03d5\u03ba\u03bb\u03bc\u03bd\u03bf\u03c0\u03b8\u03c1\u03c3\u03c4\u03c5\u03d6\u03c9\u03be\u03c8\u03b6{|}\u223c"))
SYMBOL.update(zip(range(0xA0, 0xE0), "\u20ac\u03d2\u2032\u2264\u2044\u221e\u0192\u2663\u2666\u2665\u2660\u2194\u2190\u2191\u2192\u2193"
                  "\u00b0\u00b1\u2033\u2265\u00d7\u221d\u2202\u2022\u00f7\u2260\u2261\u2248\u2026\u23d0\u23af\u21b5"
                  "\u2135\u2111\u211c\u2118\u2297\u2295\u2205\u2229\u222a\u2283\u2287\u2284\u2282\u2286\u2208\u2209"
                  "\u2220\u2207\u00ae\u00a9\u2122\u220f\u221a\u22c5\u00ac\u2227\u2228\u21d4\u21d0\u21d1\u21d2\u21d3"))
SYMBOL.update({0xE0: "\u25ca", 0xE1: "\u2329", 0xE2: "\u00ae", 0xE3: "\u00a9", 0xE4: "\u2122", 0xE5: "\u2211", 0xF1: "\u232a", 0xF2: "\u222b"})
del SYMBOL[0x60]  # radical extender, a drawing piece
# Wingdings and Webdings: only codes seen in the collected PDFs or common in Word documents, each checked by drawing the glyph.
WINGDINGS = {0x6C: "\u25cf", 0x6E: "\u25a0", 0x71: "\u2751", 0x72: "\u2752", 0x76: "\u2756", 0x9E: "\u00b7", 0x9F: "\u2022", 0xA7: "\u25aa",
             0xA8: "\u25fb", 0xD8: "\u27a2", 0xFB: "\u2717", 0xFC: "\u2713", 0xFD: "\u2612", 0xFE: "\u2611"}
WEBDINGS = {0x63: "\u2610"}
# Adobe's own private-use code points for the sans-serif registered/copyright/trademark signs.
ADOBE_PUA = {0xF8E8: "\u00ae", 0xF8E9: "\u00a9", 0xF8EA: "\u2122", 0xF6D9: "\u00ae", 0xF6DA: "\u00a9", 0xF6DB: "\u2122"}
OCR_LAYER_FONTS = ("HiddenHorzOCR", "GlyphLessFont")
_references = {}


def symbol_table(font_name):
    for key, table in (("Symbol", SYMBOL), ("Wingdings", WINGDINGS), ("Webdings", WEBDINGS)):
        if key in font_name:
            return table
    return None


def private_use_char(font_name, u):
    if u in ADOBE_PUA:
        return ADOBE_PUA[u]
    table = symbol_table(font_name)
    return table.get(u - 0xF000) if table and 0xF000 <= u <= 0xF0FF else None


def reference(base):
    # Returns (font, glyph id -> code point) for the same-named reference font, or None if it is not installed.
    if base not in _references:
        path = next((d / REFERENCE_FONTS[base] for d in FONT_DIRS if (d / REFERENCE_FONTS[base]).exists()), None) if base in REFERENCE_FONTS else None
        if path is None:
            _references[base] = None
        else:
            font = TTFont(path)
            # Symbol fonts only have a (3,0) symbol cmap, which getBestCmap skips.
            cmap = font.getBestCmap() or font["cmap"].getcmap(3, 0).cmap
            by_glyph = {}
            for cp, name in sorted(cmap.items()):
                by_glyph.setdefault(font.getGlyphID(name), cp)
            _references[base] = (font, by_glyph)
    return _references[base]


def glyph_signature(font, gid):
    # Outline points and advance width. Compiled glyph bytes are not compared: they carry hinting that differs between font versions.
    order = font.getGlyphOrder()
    if gid >= len(order):
        return None
    coords, ends, _ = font["glyf"][order[gid]].getCoordinates(font["glyf"])
    return font["hmtx"].metrics[order[gid]][0], outline_hash(list(coords), list(ends))


def outline_hash(coords, ends):
    return hashlib.md5(repr((coords, ends)).encode()).hexdigest()


EMPTY_OUTLINE = outline_hash([], [])


def character(base, cp):
    if base in ("SymbolMT", "Wingdings-Regular"):
        return private_use_char(base, cp)
    return chr(cp)


def to_unicode_cmap(mapping):
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap", "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def", "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    items = sorted(mapping.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines += [f"{len(chunk)} beginbfchar", *(f"<{gid:04X}> <{text.encode('utf-16-be').hex().upper()}>" for gid, text in chunk), "endbfchar"]
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode()


def repair(src, dst):
    """Add a verified /ToUnicode to every Identity-H TrueType font that lacks one. Writes dst only if a font was repaired."""
    pdf = fitz.open(src)
    candidates = {}
    for page in pdf:
        for xref, ext, ftype, basefont, _, encoding, *_ in page.get_fonts(full=True):
            if xref not in candidates and ftype == "Type0" and encoding == "Identity-H" and "/ToUnicode" not in pdf.xref_object(xref):
                candidates[xref] = (ext, basefont)
    if not candidates:
        pdf.close()
        return []
    # Without /ToUnicode the text trace reports U+FFFD together with the glyph id; collect the ids per font name.
    used = {}
    for page in pdf:
        for span in page.get_texttrace():
            used.setdefault(span["font"], set()).update(g for u, g, *_ in span["chars"] if u == 0xFFFD)
    fonts = []
    for xref, (ext, basefont) in candidates.items():
        base = basefont.split("+", 1)[-1]
        gids = used.get(base, set())
        if not gids:
            continue
        ref = reference(base) if ext == "ttf" else None
        if ref is None:
            fonts.append({"font": basefont, "xref": xref, "status": "no_reference_font", "glyphs": len(gids)})
            continue
        ref_font, by_glyph = ref
        embedded = TTFont(io.BytesIO(pdf.extract_font(xref)[3]))
        mapping, absent, mismatch, no_char = {}, 0, [], []
        for gid in sorted(gids):
            mine, theirs = glyph_signature(embedded, gid), glyph_signature(ref_font, gid)
            if mine is None or theirs is None or mine != theirs:
                # A subset keeps unused glyph ids as empty outlines; such ids were used by a same-named font elsewhere in the file.
                if theirs is not None and (mine is None or mine[1] == EMPTY_OUTLINE):
                    absent += 1
                else:
                    mismatch.append(gid)
                continue
            text = character(base, by_glyph[gid]) if gid in by_glyph else None
            if text is None:
                no_char.append(gid)
                continue
            mapping[gid] = text
        if mapping:
            stream = pdf.get_new_xref()
            pdf.update_object(stream, "<<>>")
            pdf.update_stream(stream, to_unicode_cmap(mapping))
            pdf.xref_set_key(xref, "ToUnicode", f"{stream} 0 R")
        fonts.append({"font": basefont, "xref": xref, "status": "repaired" if mapping else "not_repaired", "glyphs": len(gids), "mapped": len(mapping),
                      "not_in_this_font": absent, "outline_mismatch": mismatch, "no_reference_char": no_char})
    if any(f["status"] == "repaired" for f in fonts):
        dst.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(dst, garbage=0)
    pdf.close()
    return fonts


def inspect(page):
    """Per-page check of the text layer, plus the private-use characters to replace on this page."""
    chars = unmapped = invisible = ocr_layer = 0
    replace, conflicts = {}, set()
    for span in page.get_texttrace():
        n = len(span["chars"])
        chars += n
        if span["type"] == 3:
            invisible += n
        if any(name in span["font"] for name in OCR_LAYER_FONTS):
            ocr_layer += n
        for u, *_ in span["chars"]:
            if u == 0xFFFD:
                unmapped += 1
            elif 0xE000 <= u <= 0xF8FF:
                text = private_use_char(span["font"], u)
                if text is not None and replace.get(chr(u), text) != text:
                    conflicts.add(chr(u))  # same code, different meaning in two fonts on one page: leave it
                elif text is not None:
                    replace[chr(u)] = text
    # Some fonts report other codes in the trace than in extracted text. With a single symbol family on the page,
    # its table also covers codes the trace did not show.
    tables = {id(t): t for t in (symbol_table(f[3]) for f in page.get_fonts(full=True)) if t}
    if len(tables) == 1:
        table = next(iter(tables.values()))
        replace = {**{chr(0xF000 + code): text for code, text in table.items()}, **replace}
    # Adobe's own private-use code points mean the same in every font.
    replace = {**{chr(u): text for u, text in ADOBE_PUA.items()}, **replace}
    for c in conflicts:
        replace.pop(c, None)
    reasons = []
    if unmapped:
        reasons.append("unmapped_glyphs")
    if chars and max(invisible, ocr_layer) / chars > 0.5:
        reasons.append("publisher_ocr_layer")
    check = {"chars": chars, "unmapped_glyphs": unmapped, "invisible_chars": invisible, "needs_ocr": bool(reasons), "reasons": reasons}
    return check, replace


def private_use_count(text):
    return sum(0xE000 <= ord(c) <= 0xF8FF for c in text)


def normalize(text, replace):
    return text.translate(str.maketrans(replace)) if replace else text
