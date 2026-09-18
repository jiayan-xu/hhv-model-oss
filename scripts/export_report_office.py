# -*- coding: utf-8 -*-
"""把 5192 测算报告导出为 Word 全文 + 领导过会 PPT。"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor, Emu, Inches
from pptx import Presentation
from pptx.dml.color import RGBColor as PptRGB
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu as PEmu, Inches as PInches, Pt as PPt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "config.snmis_daily"
# 源稿是手写长文（非脚本生成），文件名与正文里可能残留旧结论数（如 5192）。
# 本脚本生成前会做一致性守卫：正文出现与 result.json 不符的结论数即拒绝生成（--force 可覆盖）。
SRC_CANDIDATES = [
    OUT / "固废综合热值测算报告.md",
    OUT / "固废综合热值测算报告_5192.md",  # 旧名（手写稿未改名时）
]
DOCX = OUT / "固废综合热值测算报告.docx"
PPTX = OUT / "固废综合热值测算报告.pptx"

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hhv.facts import load_facts  # noqa: E402

F = load_facts(OUT)

# 装修入炉口径（手写推导，见 NARRATIVE 说明于 export_report_submit.py）
DECOR_KCAL = 806
DECOR_KJ = 3375


def _check_source_freshness(src: Path) -> None:
    """手写源稿的结论数必须与最新跑批一致，否则拒绝生成（避免"新封面 + 旧正文"）。"""
    text = src.read_text(encoding="utf-8")
    stale = []
    for token, expect in (("5192", f"{F['q_b']:.0f}"), ("1240 大卡", f"{F['kcal_b']} 大卡"),
                          ("5003", f"{F['q_c']:.0f}")):
        if token in text and token != expect:
            stale.append(token)
    if stale:
        print(f"[export_report_office] ✗ 源稿 {src.name} 含旧结论数 {stale}，"
              f"当前应为 B={F['q_b']:.0f} kJ/{F['kcal_b']} 大卡、C={F['q_c']:.0f} kJ。")
        print("  手写稿需人工更新后再生成；确认要以旧稿生成请加 --force。")
        if "--force" not in sys.argv:
            sys.exit(1)


def _src() -> Path:
    for c in SRC_CANDIDATES:
        if c.is_file():
            return c
    sys.exit(f"[export_report_office] 找不到源稿：{SRC_CANDIDATES[0]}")

INK = RGBColor(0x2F, 0x34, 0x37)
MUTED = RGBColor(0x78, 0x77, 0x74)
ACCENT = RGBColor(0x34, 0x65, 0x38)
LINE = "EAEAEA"
HEADER_BG = "2F3437"

FONT = "微软雅黑"
FONT_SERIF = "宋体"


def set_run_font(run, name=FONT, size=11, bold=False, color=INK, east=None):
    run.bold = bold
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.name = name
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), name)
    rFonts.set(qn("w:hAnsi"), name)
    rFonts.set(qn("w:eastAsia"), east or name)


def shade_cell(cell, hex_color: str):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def set_cell_border(cell):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "C8C8C8")
        tcBorders.append(el)
    tcPr.append(tcBorders)


def add_runs(paragraph, text: str, size=11, color=INK):
    parts = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            set_run_font(run, size=size, bold=True, color=color)
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            set_run_font(run, name="Consolas", size=size - 1, color=color, east=FONT)
        else:
            run = paragraph.add_run(part)
            set_run_font(run, size=size, color=color)


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        if re.match(r"^\s*\|?\s*-{2,}", ln.replace("|", " | ")):
            if set(ln.replace("|", "").replace(" ", "").replace(":", "").replace("-", "")) == set():
                continue
        if "---" in ln and ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def build_docx():
    raw = _src().read_text(encoding="utf-8")
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.0)
    sec.right_margin = Cm(2.0)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)

    styles = doc.styles
    styles["Normal"].font.name = FONT
    styles["Normal"].font.size = Pt(11)
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    blocks = raw.split("\n")
    i = 0
    first_h1 = True
    while i < len(blocks):
        line = blocks[i]
        if line.strip() == "---":
            i += 1
            continue
        if line.startswith("# "):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_after = Pt(6)
            add_runs(p, line[2:].strip(), size=22, color=INK)
            p.runs[0].bold = True
            i += 1
            continue
        if line.startswith("## "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(18)
            p.paragraph_format.space_after = Pt(8)
            add_runs(p, line[3:].strip(), size=16, color=INK)
            for r in p.runs:
                r.bold = True
            i += 1
            continue
        if line.startswith("### "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(4)
            add_runs(p, line[4:].strip(), size=13, color=INK)
            for r in p.runs:
                r.bold = True
            i += 1
            continue
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(blocks) and not blocks[i].startswith("```"):
                buf.append(blocks[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            run = p.add_run("\n".join(buf))
            set_run_font(run, name="Consolas", size=9, east=FONT)
            continue
        if line.strip().startswith("|") and i + 1 < len(blocks) and "---" in blocks[i + 1]:
            tbl_lines = []
            while i < len(blocks) and blocks[i].strip().startswith("|"):
                tbl_lines.append(blocks[i])
                i += 1
            rows = parse_table(tbl_lines)
            if not rows:
                continue
            ncol = max(len(r) for r in rows)
            table = doc.add_table(rows=len(rows), cols=ncol)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = True
            for ri, row in enumerate(rows):
                for ci in range(ncol):
                    cell = table.cell(ri, ci)
                    cell.text = ""
                    p = cell.paragraphs[0]
                    txt = row[ci] if ci < len(row) else ""
                    add_runs(p, txt, size=9)
                    set_cell_border(cell)
                    if ri == 0:
                        shade_cell(cell, HEADER_BG)
                        for r in p.runs:
                            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                            r.bold = True
            doc.add_paragraph()
            continue
        if line.startswith("> "):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.space_after = Pt(6)
            add_runs(p, line[2:].strip(), size=11, color=MUTED)
            i += 1
            continue
        if re.match(r"^[-*] ", line):
            p = doc.add_paragraph(style="List Bullet")
            add_runs(p, line[2:].strip(), size=11)
            i += 1
            continue
        if re.match(r"^\d+\. ", line):
            p = doc.add_paragraph(style="List Number")
            add_runs(p, re.sub(r"^\d+\. ", "", line).strip(), size=11)
            i += 1
            continue
        if line.startswith("- ") and "：" in line[:20]:
            p = doc.add_paragraph()
            add_runs(p, line[2:].strip(), size=11)
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.line_spacing = 1.35
        add_runs(p, line.strip(), size=11)
        i += 1

    footer = doc.sections[0].footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run(f"常熟一般固废入炉综合热值测算报告  V1.1  ·  综合 {F['kcal_b']} 大卡  ·  装修入炉约 {DECOR_KCAL} 大卡")
    set_run_font(run, size=8, color=MUTED)

    doc.save(DOCX)
    print("word", DOCX)


# ---------- PPT ----------
W, H = PEmu(12192000), PEmu(6858000)  # 13.333" x 7.5" 16:9
C_BG = PptRGB(0xF7, 0xF6, 0xF3)
C_INK = PptRGB(0x2F, 0x34, 0x37)
C_MUTE = PptRGB(0x78, 0x77, 0x74)
C_WHITE = PptRGB(0xFF, 0xFF, 0xFF)
C_DARK = PptRGB(0x1C, 0x1F, 0x21)
C_LINE = PptRGB(0xEA, 0xEA, 0xEA)
C_GREEN = PptRGB(0x34, 0x65, 0x38)


def _set_font(run, size, bold=False, color=C_INK, name="微软雅黑"):
    run.font.size = PPt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = name
    run.font.east_asia = name


def _box(slide, l, t, w, h, text, size=18, bold=False, color=C_INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    sh = slide.shapes.add_textbox(l, t, w, h)
    tf = sh.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        sh.text_frame._txBody.bodyPr.set("anchor", "t" if anchor == MSO_ANCHOR.TOP else "ctr")
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    _set_font(run, size, bold, color)
    return sh


def _fill(shape, rgb):
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb
    shape.line.fill.background()


def add_blank(prs, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    _fill(bg, C_DARK if dark else C_BG)
    return slide


def footer_bar(slide, page, total):
    _box(slide, PEmu(457200), PEmu(6400800), PEmu(9000000), PEmu(300000),
         "常熟一般固废入炉热值  ·  2026-09-02  ·  不含克劳丽",
         size=10, color=C_MUTE)
    _box(slide, PEmu(10500000), PEmu(6400800), PEmu(1200000), PEmu(300000),
         f"{page} / {total}", size=10, color=C_MUTE, align=PP_ALIGN.RIGHT)


def build_pptx():
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    total = 12

    # 1 cover
    s = add_blank(prs, dark=True)
    _box(s, PEmu(600000), PEmu(700000), PEmu(10000000), PEmu(300000),
         "2026 日均值  ·  V1.1  ·  给领导审阅", size=14, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(600000), PEmu(1100000), PEmu(11000000), PEmu(1200000),
         "一般固废入炉综合热值", size=36, bold=True, color=C_WHITE)
    _box(s, PEmu(600000), PEmu(2400000), PEmu(5000000), PEmu(400000),
         "综合固废", size=14, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(600000), PEmu(2750000), PEmu(5000000), PEmu(900000),
         f"{F['kcal_b']} 大卡", size=48, bold=True, color=C_WHITE)
    _box(s, PEmu(600000), PEmu(3650000), PEmu(5000000), PEmu(350000),
         f"{F['q_b']:.0f} kJ/kg  ·  解法 B", size=16, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(6400000), PEmu(2400000), PEmu(5000000), PEmu(400000),
         "装修入炉（钉化验后残差）", size=14, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(6400000), PEmu(2750000), PEmu(5000000), PEmu(900000),
         "约 800 大卡", size=48, bold=True, color=C_WHITE)
    _box(s, PEmu(6400000), PEmu(3650000), PEmu(5000000), PEmu(350000),
         f"{DECOR_KJ} kJ/kg  ·  约 3400", size=16, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(600000), PEmu(4800000), PEmu(11000000), PEmu(800000),
         "窗口 2026-02-01～09-01  ·  已整日剔除克劳丽进厂 18 日\n造纸（其他）、农林按检测报告取值，装修用篮子剩余热反推。",
         size=16, color=PptRGB(0xC4, 0xC7, 0xC4))

    # 2 怎么用
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "两个数怎么用", size=28, bold=True)
    footer_bar(s, 2, total)
    items = [
        ("用 1240 大卡", "谈综合收运、这一篮子固废值多少热。相对同期生活垃圾约 1740 大卡，固废大约是它的 71%。"),
        ("用 800 大卡", "谈装修单独大约值多少热。造纸底渣、农林已按检测报告钉住，剩下的热给装修。"),
        ("不要混用", "5192 不是天越装修，也不是理文底渣。装修化验 4438～5747 大卡是筛上干样，不是入炉。"),
    ]
    for i, (t, b) in enumerate(items):
        top = 1200000 + i * 1500000
        card = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(600000), PEmu(top), PEmu(11000000), PEmu(1350000))
        card.fill.solid()
        card.fill.fore_color.rgb = C_WHITE
        card.line.color.rgb = C_LINE
        _box(s, PEmu(800000), PEmu(top + 150000), PEmu(10500000), PEmu(400000), t, size=20, bold=True)
        _box(s, PEmu(800000), PEmu(top + 600000), PEmu(10500000), PEmu(600000), b, size=16, color=C_MUTE)

    # 3 范围口径
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "数据范围与口径", size=28, bold=True)
    footer_bar(s, 3, total)
    rows = [
        ("时间", "2026-02-01～09-01，213 日。历史尺子用 2024 二期纯烧，不用 2025。"),
        ("蒸汽", "生产日报一（流量）；指标日报（温压氧排烟）。只读导出，未改填报。"),
        ("固废是谁", "运营台白名单：货名含「其他」，跳过环卫所/城东。大件算生活垃圾。"),
        ("克劳丽", "18 日 / 387 车 / 8124 吨，整日剔除。含克劳丽对照为 5478（1309 大卡）。"),
        ("合格周", "24 周进回归（一期有效日 183/213）。内部验证通过。闸门 DUAL 9.5%。"),
    ]
    for i, (a, b) in enumerate(rows):
        top = 1050000 + i * 950000
        _box(s, PEmu(600000), PEmu(top), PEmu(2200000), PEmu(800000), a, size=16, bold=True, color=C_GREEN)
        _box(s, PEmu(2900000), PEmu(top), PEmu(8600000), PEmu(850000), b, size=16)

    # 4 逻辑
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "测算逻辑（白话）", size=28, bold=True)
    footer_bar(s, 4, total)
    _box(s, PEmu(600000), PEmu(1100000), PEmu(11000000), PEmu(700000),
         "混料热值 = 生活垃圾热值 × (1 − α) + 固废热值 × α",
         size=22, bold=True)
    steps = [
        "1  蒸汽带走的热 ÷ 锅炉效率（约 86%～87%）÷ 入炉吨  →  这一锅混料有多热",
        "2  筛掉低负荷、缺测点、克劳丽进厂日；一周有效日不满 5 天不进回归",
        "3  α = 当周白名单固废 / 二期入炉。24 周 α 从 2% 到 30%，周均 19%",
        "4  一期当周当生活垃圾尺子，乘刻度 k=1.10 换到二期表计",
        "5  固废掺得越多、混料偏离尺子越多，把固废热值拧出来  →  5192",
    ]
    for i, t in enumerate(steps):
        _box(s, PEmu(600000), PEmu(2000000 + i * 750000), PEmu(11000000), PEmu(700000), t, size=18)

    # 5 三解法
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "三个解法：报 B，并报 C", size=28, bold=True)
    footer_bar(s, 5, total)
    cards = [
        ("解法 B  主推", "综合 5192\n1240 大卡", "一期同周 × k\n区间 1077～1418 大卡"),
        ("解法 C  并报", "综合 5003\n1195 大卡", "2024 同炉周对齐\n与 B 差 45 大卡"),
        ("解法 A  校验", "综合 5433\n1298 大卡", "自由回归，区间太宽\n不能单独报价"),
    ]
    for i, (h, n, f) in enumerate(cards):
        left = 500000 + i * 3800000
        rec = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(left), PEmu(1200000), PEmu(3500000), PEmu(4300000))
        rec.fill.solid()
        rec.fill.fore_color.rgb = C_WHITE
        rec.line.color.rgb = C_LINE
        _box(s, PEmu(left + 200000), PEmu(1400000), PEmu(3100000), PEmu(500000), h, size=16, bold=True, color=C_GREEN)
        _box(s, PEmu(left + 200000), PEmu(2000000), PEmu(3100000), PEmu(1600000), n, size=28, bold=True)
        _box(s, PEmu(left + 200000), PEmu(3900000), PEmu(3100000), PEmu(1200000), f, size=14, color=C_MUTE)

    # 6 验证
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "内部验证：通过", size=28, bold=True)
    footer_bar(s, 6, total)
    gates = [
        ("V0 双锚", "B 与 C 相差 189 kJ，门限 500。换一把生活垃圾尺子，固废数只挪不到 200。"),
        ("V2 高掺烧", "固废掺得最多的那组，隐含装修热值 5224，仍落在 4509～5936 里。"),
        ("V3 踢残差周", "丢掉物料对不上的 7 周后再算，得 5010，只挪 182。不是那几周撑出来的。"),
        ("化验不否决", "化验加权 13916 对不上 5192。化验是样品，模型是入炉混合。禁止用装修干样否定综合数。"),
    ]
    for i, (h, b) in enumerate(gates):
        top = 1100000 + i * 1200000
        _box(s, PEmu(600000), PEmu(top), PEmu(2800000), PEmu(900000), h, size=18, bold=True, color=C_GREEN)
        _box(s, PEmu(3600000), PEmu(top), PEmu(7900000), PEmu(1000000), b, size=16)

    # 7 装修结论
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "装修入炉：约 800 大卡", size=28, bold=True)
    footer_bar(s, 7, total)
    _box(s, PEmu(600000), PEmu(1100000), PEmu(11000000), PEmu(600000),
         "领导口径：造纸（其他）、农林钉检测报告，装修用残差反推。", size=18, color=C_MUTE)
    rec = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(600000), PEmu(1900000), PEmu(11000000), PEmu(3800000))
    rec.fill.solid()
    rec.fill.fore_color.rgb = C_WHITE
    rec.line.color.rgb = C_LINE
    _box(s, PEmu(900000), PEmu(2150000), PEmu(4500000), PEmu(400000), "点估计", size=14, color=C_MUTE)
    _box(s, PEmu(900000), PEmu(2550000), PEmu(5000000), PEmu(900000), "3375 kJ/kg", size=36, bold=True)
    _box(s, PEmu(900000), PEmu(3450000), PEmu(5000000), PEmu(500000), "806 大卡  ·  对外说约 800", size=18)
    _box(s, PEmu(6200000), PEmu(2150000), PEmu(5000000), PEmu(3200000),
         "解法 C → 733 大卡\n综合数下限 → 540 大卡\n综合数上限 → 1100 大卡\n\n把握大约 700～1100 大卡\n会上先报 800 大卡",
         size=16)

    # 8 钉化验表
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(350000), PEmu(11000000), PEmu(450000),
         "钉死的检测报告（原值不打折）", size=26, bold=True)
    footer_bar(s, 8, total)
    headers = ["种类", "报告", "大卡", "吨", "占比"]
    data = [
        ["造纸（其他）理文底渣", "2026年2月", "1626", "10,784", "16.1%"],
        ["农林 雷博尔", "2026年3月", "2792", "9,582", "14.3%"],
        ["其他工业 东升", "2026年1月", "892", "1,728", "2.6%"],
        ["华衍沼渣", "2026年5月", "918", "3,223", "4.8%"],
        ["格栅 苏水中法", "2026年6月", "1958", "532", "0.8%"],
        ["装修（反推，不钉化验）", "—", "806", "40,949", "61.3%"],
    ]
    col_w = [3800000, 2200000, 1600000, 1800000, 1500000]
    lefts = [600000]
    for w in col_w[:-1]:
        lefts.append(lefts[-1] + w)
    top0 = 950000
    row_h = 720000
    for ci, h in enumerate(headers):
        cell = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(lefts[ci]), PEmu(top0), PEmu(col_w[ci]), PEmu(row_h))
        cell.fill.solid()
        cell.fill.fore_color.rgb = C_DARK
        cell.line.fill.background()
        _box(s, PEmu(lefts[ci] + 80000), PEmu(top0 + 180000), PEmu(col_w[ci] - 120000), PEmu(400000),
             h, size=13, bold=True, color=C_WHITE)
    for ri, row in enumerate(data):
        top = top0 + (ri + 1) * row_h
        bg = C_WHITE if ri < 5 else PptRGB(0xED, 0xF3, 0xEC)
        for ci, val in enumerate(row):
            cell = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(lefts[ci]), PEmu(top), PEmu(col_w[ci]), PEmu(row_h))
            cell.fill.solid()
            cell.fill.fore_color.rgb = bg
            cell.line.color.rgb = C_LINE
            _box(s, PEmu(lefts[ci] + 80000), PEmu(top + 180000), PEmu(col_w[ci] - 120000), PEmu(400000),
                 val, size=13, bold=(ri == 5))

    # 9 差五倍
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "装修化验为什么比入炉高五倍", size=26, bold=True)
    footer_bar(s, 9, total)
    left_card = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(600000), PEmu(1200000), PEmu(5200000), PEmu(4200000))
    left_card.fill.solid()
    left_card.fill.fore_color.rgb = C_WHITE
    left_card.line.color.rgb = C_LINE
    _box(s, PEmu(850000), PEmu(1450000), PEmu(4700000), PEmu(400000), "化验室（筛上干样）", size=16, color=C_MUTE)
    _box(s, PEmu(850000), PEmu(1950000), PEmu(4700000), PEmu(800000), "4438～5747 大卡", size=28, bold=True)
    _box(s, PEmu(850000), PEmu(2900000), PEmu(4700000), PEmu(2000000),
         "天越 7 月 4438 大卡\n苏再投 4 月 5747 大卡\n拣过的轻质筛上物，偏干", size=16, color=C_MUTE)
    right_card = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(6100000), PEmu(1200000), PEmu(5200000), PEmu(4200000))
    right_card.fill.solid()
    right_card.fill.fore_color.rgb = C_WHITE
    right_card.line.color.rgb = C_LINE
    _box(s, PEmu(6350000), PEmu(1450000), PEmu(4700000), PEmu(400000), "入炉（湿料残差）", size=16, color=C_MUTE)
    _box(s, PEmu(6350000), PEmu(1950000), PEmu(4700000), PEmu(800000), "约 800 大卡", size=28, bold=True)
    _box(s, PEmu(6350000), PEmu(2900000), PEmu(4700000), PEmu(2000000),
         "含水、灰分、筛下不可燃\n坑内掺混之后的反平衡\n造纸、农林化验可当入炉用\n装修化验当不了", size=16, color=C_MUTE)

    # 10 克劳丽
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "为什么从主口径拿掉克劳丽", size=26, bold=True)
    footer_bar(s, 10, total)
    _box(s, PEmu(600000), PEmu(1200000), PEmu(11000000), PEmu(900000),
         "18 天、387 车、8124 吨。化妆品固废热值高于装修/底渣混料，又集中在高掺烧周，杠杆太大，且无自家氧弹。",
         size=18)
    pair = [
        ("含克劳丽（对照）", "5478 kJ\n1309 大卡\n27 周"),
        ("不含（现主口径）", "5192 kJ\n1240 大卡\n24 周"),
    ]
    for i, (h, n) in enumerate(pair):
        left = 600000 + i * 5600000
        rec = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(left), PEmu(2400000), PEmu(5200000), PEmu(3000000))
        rec.fill.solid()
        rec.fill.fore_color.rgb = C_WHITE
        rec.line.color.rgb = C_LINE
        _box(s, PEmu(left + 300000), PEmu(2600000), PEmu(4600000), PEmu(500000), h, size=16, color=C_MUTE)
        _box(s, PEmu(left + 300000), PEmu(3200000), PEmu(4600000), PEmu(1800000), n, size=24, bold=True)

    # 11 不要说
    s = add_blank(prs)
    _box(s, PEmu(600000), PEmu(400000), PEmu(11000000), PEmu(500000),
         "会上不要这么说", size=28, bold=True)
    footer_bar(s, 11, total)
    nos = [
        "「固废热值是 5192，所以装修也是 5192。」",
        "「化验装修 4000 多大卡，入炉也是 4000 多大卡。」",
        "「化验 2 万，模型 5192，模型错了。」",
        "「去年 6005、今年 5192，品质下降了三成。」",
    ]
    for i, t in enumerate(nos):
        top = 1200000 + i * 1100000
        rec = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, PEmu(600000), PEmu(top), PEmu(11000000), PEmu(950000))
        rec.fill.solid()
        rec.fill.fore_color.rgb = C_WHITE
        rec.line.color.rgb = C_LINE
        _box(s, PEmu(900000), PEmu(top + 250000), PEmu(10400000), PEmu(500000), t, size=20)

    # 12 锁定
    s = add_blank(prs, dark=True)
    _box(s, PEmu(600000), PEmu(1400000), PEmu(11000000), PEmu(500000),
         "本次锁定", size=18, color=PptRGB(0xA8, 0xAD, 0xA8))
    _box(s, PEmu(600000), PEmu(2000000), PEmu(11000000), PEmu(900000),
         f"综合 {F['kcal_b']} 大卡    装修入炉约 {DECOR_KCAL} 大卡", size=28, bold=True, color=C_WHITE)
    _box(s, PEmu(600000), PEmu(3200000), PEmu(11000000), PEmu(1400000),
         "收到基低位  ·  2026 日均值  ·  不含克劳丽\n不是商务报价单，价格另走合同与财务口径。",
         size=18, color=PptRGB(0xC4, 0xC7, 0xC4))

    prs.save(PPTX)
    print("ppt", PPTX)


if __name__ == "__main__":
    _check_source_freshness(_src())
    build_docx()
    build_pptx()
