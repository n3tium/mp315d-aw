#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает manual_mp315d-aw_ru.pdf: заменяет японские текстовые блоки
на русские переводы из translations.json, сохраняя исходное оформление."""
import json, re

import pymupdf

SRC = "manual_mp315d-aw.pdf"
DST = "manual_mp315d-aw_ru.pdf"
TRANS = "translations.json"

JP_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]')


def span_text(s):
    return "".join(c["c"] for c in s["chars"])


def main():
    trans = json.load(open(TRANS, encoding="utf-8"))
    doc = pymupdf.open(SRC)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    replaced = kept_original = redacted = 0

    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            bbox = pymupdf.Rect(b["bbox"])
            if not bbox.is_valid or bbox.is_empty:
                continue
            # текст и параметры блока
            texts, sizes, colors, bold = [], set(), set(), False
            has_jp = False
            for l in b["lines"]:
                for s in l["spans"]:
                    t = s.get("text") or "".join(c["c"] for c in s.get("chars", []))
                    if JP_RE.search(t):
                        has_jp = True
                    texts.append(t)
                    sizes.add(round(s["size"], 2))
                    colors.add(s["color"])
                    if s["flags"] & 2 ** 4 or "Bold" in s["font"] or "bold" in s["font"].lower():
                        bold = True
            if not has_jp:
                continue
            raw = "".join(texts)
            ru_lines = []
            for part in re.split(r"\n", raw):
                key = part.strip()
                if not key:
                    continue
                tr = trans.get(key)
                if tr and not JP_RE.search(tr):
                    ru_lines.append(tr)
                elif tr:
                    ru_lines.append(re.sub(r"\s+", " ", tr).strip())
                else:
                    ru_lines.append(part.strip())
            ru_lines = [x for x in ru_lines if x]
            if not ru_lines:
                continue
            all_ru = " ".join(ru_lines)
            if JP_RE.search(all_ru):
                kept_original += 1
                continue  # не переводили блок с остатками японского — оставляем как есть

            # удалить оригинальный текст (закрасить белым)
            page.add_redact_annot(bbox + (-1, -1, 1, 1), fill=(1, 1, 1))
            redacted += 1

            size = min(max(min(sizes) * 0.95, 5.5), 12.0)
            color = next(iter(colors), 0)
            rc = pymupdf.Rect(bbox)
            try:
                FFILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                remainder = page.insert_textbox(
                    rc, all_ru, fontsize=size, fontname="djv",
                    fontfile=FFILE, color=color, align=0, rotate=0)
                if remainder < 0:
                    # не влезло — уменьшаем шрифт, потом расширяем бокс
                    while remainder < 0 and size > 5.0:
                        size -= 0.5
                        rc = pymupdf.Rect(bbox)
                        remainder = page.insert_textbox(
                            rc, all_ru, fontsize=size, fontname="djv",
                            fontfile=FFILE, color=color, align=0, rotate=0)
                    if remainder < 0:
                        grow = pymupdf.Rect(bbox.x0, bbox.y0,
                                            min(bbox.x1 + 40, page.rect.x1),
                                            min(bbox.y1 + (page.rect.y1 - bbox.y0) * 0.5, page.rect.y1))
                        page.insert_textbox(grow, all_ru, fontsize=size,
                                               fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                                               color=color, align=0, rotate=0)
                replaced += 1
            except Exception as e:
                print(f"page {pno}: insert failed: {e}")

        # применить закрашивания для этой страницы
        if not page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE):
            pass

    doc.subset_fonts()
    doc.save(DST, garbage=4, deflate=True)
    print(f"OK pages={len(doc)} blocks_translated={replaced} skipped_jp_left={kept_original} redacted={redacted}")


if __name__ == "__main__":
    main()
