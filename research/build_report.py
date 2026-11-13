# -*- coding: utf-8 -*-
"""生成参赛方案书 PDF（A4，含封面、摘要、目录、八章正文、参考文献、附录）。

用法：python research/build_report.py [输出路径]
默认输出到主项目目录下的「赛题二AI4S方案书.pdf」。
"""

import re
import sys
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak,
                               PageTemplate, Paragraph, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import report_content_a as A
import report_content_b as B

DEFAULT_OUT = Path(r"C:\Users\24543\Doubao\chats\2026-10-11\new-chat\赛题二AI4S方案书.pdf")

FONT_DIR = Path(r"C:\Windows\Fonts")


def register_fonts():
    """优先用微软雅黑（覆盖字符更全），失败回退黑体/宋体。"""
    def try_reg(name, filename, idx=None):
        p = FONT_DIR / filename
        if not p.exists():
            return None
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont(name, str(p)))
            else:
                pdfmetrics.registerFont(TTFont(name, str(p), subfontIndex=idx))
            return name
        except Exception as exc:
            print("字体注册失败 %s: %s" % (filename, exc))
            return None

    body = try_reg("CN", "msyh.ttc", 0) or try_reg("CN", "simsun.ttc", 0) or try_reg("CN", "simhei.ttf")
    bold = try_reg("CNB", "msyhbd.ttc", 0) or try_reg("CNB", "simhei.ttf") or body
    return body, bold


BODY_FONT, BOLD_FONT = register_fonts()


def clean(t):
    """替换 TTF 里可能缺字形的字符，避免出现黑块。"""
    return (t.replace("\u2212", "-")
             .replace("\u00a0", " ")
             .replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


KIN = set("，。、；：！？）】》」』”")
OPEN = set("（【《「『“")


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\.\-%/:]*|.", re.S)


def wrap_lines(text, first_w, rest_w, font, size, slack=0.985):
    """按字宽手工断行，遇前置标点则带上一字同行，避免标点落在行首。"""
    text = text.replace("\u2212", "-").replace("\u00a0", " ")
    lines, cur, cur_w, limit = [], "", 0.0, first_w * slack
    for tok in TOKEN_RE.findall(text):
        w = stringWidth(tok, font, size)
        if cur and cur_w + w > limit:
            if tok in KIN or (cur and cur[-1] in OPEN):
                last = cur[-1]
                if len(cur) > 1:
                    lines.append(cur[:-1])
                    cur = last
                    cur_w = stringWidth(last, font, size)
                else:
                    lines.append(cur)
                    cur, cur_w = "", 0.0
                limit = rest_w * slack
            else:
                lines.append(cur)
                cur, cur_w = "", 0.0
                limit = rest_w * slack
        cur += tok
        cur_w += w
    if cur:
        lines.append(cur)
    return lines


def para(text, style, avail_w):
    """交给 Paragraph 自然断行（保持两端对齐）；手工断行会破坏对齐并产生孤行。"""
    return Paragraph(clean(text), style)


def styles():
    s = {}
    s["cover_title"] = ParagraphStyle("cover_title", fontName=BOLD_FONT, fontSize=25, leading=36,
                                      alignment=TA_CENTER, textColor=colors.HexColor("#0B2E4A"))
    s["cover_sub"] = ParagraphStyle("cover_sub", fontName=BODY_FONT, fontSize=13, leading=22,
                                    alignment=TA_CENTER, textColor=colors.HexColor("#33506B"))
    s["cover_meta"] = ParagraphStyle("cover_meta", fontName=BODY_FONT, fontSize=10.5, leading=20,
                                     alignment=TA_CENTER, textColor=colors.HexColor("#41505E"))
    s["h1"] = ParagraphStyle("h1", fontName=BOLD_FONT, fontSize=15, leading=24,
                             spaceBefore=16, spaceAfter=9, textColor=colors.HexColor("#0B2E4A"))
    s["h2"] = ParagraphStyle("h2", fontName=BOLD_FONT, fontSize=11.5, leading=19,
                             spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1A3E5C"))
    s["body"] = ParagraphStyle("body", fontName=BODY_FONT, fontSize=10.5, leading=17.5,
                               alignment=TA_JUSTIFY, firstLineIndent=21, spaceAfter=5,
                               wordWrap="CJK")
    s["abs"] = ParagraphStyle("abs", fontName=BODY_FONT, fontSize=10.5, leading=18,
                              alignment=TA_JUSTIFY, firstLineIndent=21, spaceAfter=6, wordWrap="CJK")
    s["ref"] = ParagraphStyle("ref", fontName=BODY_FONT, fontSize=9, leading=14,
                              alignment=TA_JUSTIFY, leftIndent=22, firstLineIndent=-22,
                              spaceAfter=3, wordWrap="CJK")
    s["caption"] = ParagraphStyle("caption", fontName=BODY_FONT, fontSize=9, leading=14,
                                  alignment=TA_CENTER, spaceBefore=3, spaceAfter=9,
                                  textColor=colors.HexColor("#43525F"))
    s["code"] = ParagraphStyle("code", fontName=body_for_code(), fontSize=8.5, leading=13,
                               alignment=TA_LEFT, backColor=colors.HexColor("#F4F6F8"),
                               borderPadding=(8, 8, 8, 8), leftIndent=6, wordWrap="CJK")
    s["toc"] = ParagraphStyle("toc", fontName=BODY_FONT, fontSize=10.5, leading=21,
                              alignment=TA_LEFT, leftIndent=8, wordWrap="CJK")
    return s


def body_for_code():
    return BODY_FONT


def make_table(spec, avail_w, st):
    header = spec["header"]
    rows = spec["rows"]
    widths = [w * avail_w for w in spec["widths"]]
    data = [[Paragraph(clean(h), cell_style(st, bold=True)) for h in header]]
    for r in rows:
        data.append([Paragraph(clean(c), cell_style(st, bold=False)) for c in r])
    tb = Table(data, colWidths=widths, repeatRows=1, hAlign="CENTER")
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EFF5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0B2E4A")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9FB2C2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    cap = Paragraph(clean(spec["caption"]), st["caption"])
    return KeepTogether([cap, tb, Spacer(1, 6)])


def cell_style(st, bold=False):
    return ParagraphStyle("cell", fontName=BOLD_FONT if bold else BODY_FONT, fontSize=8.5,
                          leading=12.5, alignment=TA_CENTER if bold else TA_LEFT, wordWrap="CJK")


def build(out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(out_path), pagesize=A4,
                          leftMargin=70.9, rightMargin=70.9, topMargin=70.9, bottomMargin=64)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=footer)])
    st = styles()
    story = []

    # ---------- 封面 ----------
    story.append(Spacer(1, 70))
    story.append(Paragraph(clean(A.TITLE), st["cover_title"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(clean(A.SUBTITLE), st["cover_sub"]))
    story.append(Spacer(1, 34))
    line = Table([[""]], colWidths=[doc.width * 0.62], rowHeights=[2])
    line.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 1.2, colors.HexColor("#1F6FB2"))]))
    story.append(line)
    story.append(Spacer(1, 46))
    for ln in A.META_LINES:
        story.append(Paragraph(clean(ln), st["cover_meta"]))
    story.append(PageBreak())

    # ---------- 摘要 ----------
    story.append(Paragraph("摘　要", st["h1"]))
    story.append(para(A.ABSTRACT, st["abs"], doc.width))
    story.append(Spacer(1, 4))
    story.append(para(A.KEYWORDS, st["abs"], doc.width))
    story.append(PageBreak())

    # ---------- 目录 ----------
    story.append(Paragraph("目　录", st["h1"]))
    toc_items = [
        "一、研究问题陈述与选题依据",
        "二、文献综述",
        "三、研究设计与技术路线",
        "四、数据来源与处理说明",
        "五、计算与实验执行过程说明",
        "六、结果分析与验证",
        "七、研究结论与局限性说明",
        "八、人工智能使用说明",
        "参考文献",
        "附录 A　智能体运行台账（节选）",
        "附录 B　复现步骤",
    ]
    for it in toc_items:
        story.append(Paragraph(clean(it), st["toc"]))
    story.append(PageBreak())

    # ---------- 正文 ----------
    avail_w = doc.width
    for blocks in (A.BLOCKS_A, B.BLOCKS_B):
        for kind, payload in blocks:
            if kind == "h1":
                story.append(Paragraph(clean(payload), st["h1"]))
            elif kind == "h2":
                story.append(Paragraph(clean(payload), st["h2"]))
            elif kind == "p":
                story.append(para(payload, st["body"], doc.width))
            elif kind == "ref":
                story.append(para(payload, st["ref"], doc.width))
            elif kind == "code":
                story.append(Paragraph(clean(payload).replace("\n", "<br/>"), st["code"]))
                story.append(Spacer(1, 8))
            elif kind == "table":
                story.append(make_table(payload, avail_w, st))
            elif kind == "img":
                path, caption, width_cm = payload
                w = width_cm * 28.3465
                with PILImage.open(path) as im:
                    iw, ih = im.size
                h = w * ih / iw
                if h > 300:
                    h = 300
                    w = h * iw / ih
                img = Image(str(path), width=w, height=h)
                img.hAlign = "CENTER"
                story.append(KeepTogether([img, Paragraph(clean(caption), st["caption"])]))

    doc.build(story)
    return out_path


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(BODY_FONT, 9)
    canvas.setFillColor(colors.HexColor("#5B6A78"))
    canvas.drawCentredString(A4[0] / 2, 36, "第 %d 页" % doc.page)
    canvas.setStrokeColor(colors.HexColor("#C8D4DE"))
    canvas.setLineWidth(0.4)
    canvas.line(70.9, 52, A4[0] - 70.9, 52)
    canvas.restoreState()


def count_chars():
    text = A.ABSTRACT + A.KEYWORDS
    for blocks in (A.BLOCKS_A, B.BLOCKS_B):
        for kind, payload in blocks:
            if kind in ("h1", "h2", "p", "ref", "code"):
                text += payload
            elif kind == "table":
                text += payload["caption"] + "".join(payload["header"])
                text += "".join("".join(r) for r in payload["rows"])
    cn = len(re.findall(r"[\u4e00-\u9fa5]", text))
    en = len(re.findall(r"[A-Za-z]+", text))
    return cn, en


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    cn, en = count_chars()
    print("中文字符 %d，英文词 %d（合计约 %d 字）" % (cn, en, cn + en))
    p = build(out)
    print("生成 %s（%d 字节）" % (p, p.stat().st_size))

