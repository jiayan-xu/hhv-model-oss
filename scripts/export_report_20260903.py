# -*- coding: utf-8 -*-
"""按 固废热值测算_20260903.pptx 七节结构写 Word 报告。

数字与当日入炉反推定价表对齐：综合 1188、装修 720。
不覆盖 PPT 模版本身。
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parents[1] / "outputs" / "config.snmis_daily"
DOCX = OUT / "固废热值测算报告_20260903.docx"

FONT = "微软雅黑"
INK = RGBColor(0x17, 0x21, 0x3A)
MUTED = RGBColor(0x6B, 0x7C, 0x93)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x17, 0x8A, 0x50)
ORANGE = RGBColor(0xD9, 0x77, 0x06)


def font(run, size=11, bold=False, color=INK, name=FONT):
  run.bold = bold
  run.font.size = Pt(size)
  run.font.color.rgb = color
  run.font.name = name
  rPr = run._element.get_or_add_rPr()
  rPr.get_or_add_rFonts().set(qn("w:eastAsia"), name)
  rPr.get_or_add_rFonts().set(qn("w:ascii"), name)
  rPr.get_or_add_rFonts().set(qn("w:hAnsi"), name)


def p(doc, text, size=11, bold=False, space=6, color=INK, before=0):
  para = doc.add_paragraph()
  para.paragraph_format.space_after = Pt(space)
  para.paragraph_format.space_before = Pt(before)
  para.paragraph_format.line_spacing = 1.28
  run = para.add_run(text)
  font(run, size=size, bold=bold, color=color)
  return para


def shade(cell, hex_color):
  shd = OxmlElement("w:shd")
  shd.set(qn("w:fill"), hex_color)
  shd.set(qn("w:val"), "clear")
  cell._tc.get_or_add_tcPr().append(shd)


def borders(cell):
  tcPr = cell._tc.get_or_add_tcPr()
  tcBorders = OxmlElement("w:tcBorders")
  for edge in ("top", "left", "bottom", "right"):
    el = OxmlElement(f"w:{edge}")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), "4")
    el.set(qn("w:color"), "D0D5DD")
    tcBorders.append(el)
  tcPr.append(tcBorders)


def table(doc, header, rows, highlight_last=False, header_fill="17213A"):
  t = doc.add_table(rows=1 + len(rows), cols=len(header))
  t.autofit = True
  for i, h in enumerate(header):
    cell = t.cell(0, i)
    cell.text = ""
    run = cell.paragraphs[0].add_run(h)
    font(run, size=9, bold=True, color=WHITE)
    shade(cell, header_fill)
    borders(cell)
  for ri, row in enumerate(rows):
    last = highlight_last and ri == len(rows) - 1
    for ci, val in enumerate(row):
      cell = t.cell(ri + 1, ci)
      cell.text = ""
      run = cell.paragraphs[0].add_run(str(val))
      font(run, size=9, bold=last, color=ORANGE if last else INK)
      if last:
        shade(cell, "FFF4E5")
      elif ri % 2 == 1:
        shade(cell, "F7F8FA")
      borders(cell)
  spacer = doc.add_paragraph()
  spacer.paragraph_format.space_after = Pt(8)


def build():
  doc = Document()
  sec = doc.sections[0]
  sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
  sec.left_margin = sec.right_margin = Cm(2.1)
  sec.top_margin = sec.bottom_margin = Cm(1.8)

  p(doc, "一般固废入炉热值测算报告", size=20, bold=True, space=4)
  p(doc, "2026-09-03  ·  窗口 2026-02-01～09-01  ·  池内混料模型  ·  不含克劳丽",
    size=10, color=MUTED, space=4)
  p(doc, "结构对齐模版《固废热值测算_20260903》七节。综合 1188 大卡、装修入炉约 720 大卡。",
    size=10, color=MUTED, space=14)

  p(doc, "一、结论", size=14, bold=True, space=8, before=2)
  table(doc, ["项目", "热值", "说明"], [
    ["综合固废（主报价）", "1188 大卡/公斤（4972 kJ/kg）",
     "解法 B；95% 区间约 1033～1362 大卡（4324～5700 kJ）"],
    ["装修入炉（钉化验后残差）", "约 720 大卡/公斤（3016 kJ/kg）",
     "点估计 720；综合区间传到装修约 468～1004 大卡"],
    ["同期生活垃圾", "约 1740 大卡/公斤", "一期尺子 × 刻度 k=1.10"],
  ])
  p(doc, "1188 谈「这一篮子工业固废混在一起值多少热」。720 单独看装修：造纸、农林按检测报告钉住，装修吃剩余的热。两个数不要混。综合不是地磅上「未分类」的货，也不是第五种固废。", size=11, space=6)
  p(doc, "两个独立锚相互验证：解法 B 与解法 C 相差 43 kJ（约 10 大卡）。已整日剔除克劳丽。垃圾池混料物理模型 + 化验值固定 + 装修残差反推。", size=11, space=10, color=GREEN)

  p(doc, "二、数据与口径", size=14, bold=True, space=8, before=8)
  table(doc, ["项", "口径"], [
    ["时间", "2026-02-01～09-01，213 日。历史尺子用 2024 年二期纯烧生活垃圾，不用 2025（当年已掺烧）。"],
    ["蒸汽", "SNMIS 只读导出：生产日报一（流量）+ 指标日报（温压氧排烟）。未改填报、未点提交。"],
    ["固废", "运营台白名单；货名含「其他」，跳过环卫所、城东。大件粉碎、生活源、厨余、机扫算生活垃圾。"],
    ["克劳丽", "18 日 / 387 车 / 8124 吨，偶发扰动，整日剔除（不是只扣吨）。"],
    ["垃圾池", "滞后扫描 0～7 天取最优；掺烧比例 α 取进厂成分 3 周滚动均值。"],
    ["样本", "24 周进回归。闸门 DUAL 9.5%，主报 B、并报 C。"],
    ["吨位", "窗口内白名单固废约 8.03 万 t；剔克劳丽日后用于反推装修的篮子 66,797 t。"],
  ])
  p(doc, "收到基低位、入炉态。不是干基，不是单车化验，不是商务报价单。", size=11, space=10)

  p(doc, "三、综合数怎么算：公式与推导", size=14, bold=True, space=8, before=8)
  p(doc, "锅炉烧掉的是混料。蒸汽热量和入炉吨能算出「这一锅有多热」，算不出固废自己有多热。一期几乎不掺工业固废，当生活垃圾尺子。固废掺得越多、混料偏离尺子越多，把固废拧出来。", size=11, space=8)

  p(doc, "第一步  锅炉反平衡 → 混料热值 Q混", size=12, bold=True, space=4)
  p(doc, "Q入炉 = D×(h汽−h水) / η　　η = 1 − (q2+q3+q4+q5+q6)　　Q混 = Q入炉 / M入炉", size=11, space=4)
  p(doc, "D 为蒸汽流量，h 为焓差（查蒸汽表），η 为反平衡效率（约 86%～87%，由烟气氧量和排烟温度逐日算出），M 为入炉吨。", size=11, color=MUTED, space=8)

  p(doc, "第二步  一期纯烧当尺子 → Q生活（锚）", size=12, bold=True, space=4)
  p(doc, "Q生活锚(周) = Q生活一期(周) × k　　k = median(Q二期历史 / Q一期当月)　　本窗口 k = 1.10", size=11, space=4)
  p(doc, "一期几乎纯烧生活垃圾，热值可直接测出；二期两年前有纯烧历史段，用同月份交叉验证求两炉表计刻度比 k。", size=11, color=MUTED, space=8)

  p(doc, "第三步  掺烧比例分解 → 回归解出 Q固废", size=12, bold=True, space=4)
  p(doc, "Q混(周) = Q生活(周)×(1−α) + Q固废×α　　α(周) = m固废 / M入炉（3 周平滑）", size=11, space=4)
  p(doc, "Huber 稳健回归得 Q固废 = 4972 kJ/kg（1188 大卡）。Bootstrap 2000 次，95% 区间 4324～5700 kJ/kg。α 取进厂成分 3 周滚动均值，对应混料池物理真实。", size=11, space=8)

  table(doc, ["解法", "综合固废", "角色"], [
    ["B  一期同周 × k", "4972 kJ / 1188 大卡", "主报"],
    ["C  2024 同炉周对齐", "4929 kJ / 1177 大卡", "并报，与 B 差 43 kJ（10 大卡）"],
    ["A  自由回归", "5083 kJ / 1214 大卡", "校验，区间太宽，不单独报"],
  ])
  p(doc, "核心改进：α 池内平滑后，独立双锚收敛到差 43 kJ——公式没变，回归变量噪声消了。旧数（约 1240 大卡）落在新区间内。闸门 DUAL，故 B、C 并报、主推仍报 B。化验加权对不上 4972：化验是样品，模型是入炉混合，不作为否决。", size=11, space=6)
  p(doc, "掺烧闭合周均 12.1%，库存口径 12.8%，已作诊断、未改回归周。高掺烧周仍留在回归里。", size=11, space=10, color=MUTED)

  p(doc, "四、装修：钉检测报告，吃剩余的热", size=14, bold=True, space=8, before=8)
  p(doc, "造纸（其他）、农林按检测报告原值；装修用篮子剩余热反推。与综合数同一篮子（已剔克劳丽）。华衍沼渣单独钉自家化验，不并进装修。", size=11, space=8)
  table(doc, ["种类", "取值", "大卡/公斤", "吨", "占比"], [
    ["造纸（其他）理文底渣", "2026 年 2 月检测报告", "1626", "10,784", "16.1%"],
    ["农林 雷博尔", "2026 年 3 月检测报告", "2792", "9,582", "14.3%"],
    ["其他工业 东升", "2026 年 1 月检测报告", "892", "1,728", "2.6%"],
    ["华衍沼渣", "2026 年 5 月检测报告", "918", "3,223", "4.8%"],
    ["格栅 苏水中法", "2026 年 6 月检测报告", "1958", "532", "0.8%"],
    ["装修（反推，不钉化验）", "篮子剩余热 / 装修吨", "约 720", "40,949", "61.3%"],
  ], highlight_last=True)
  p(doc, "算法：篮子总热 = 4972 × 66,797 吨；扣掉上表已钉种类后，除以装修吨。点估计 3016 kJ/kg（720 大卡）。解法 C 对应约 704 大卡。只钉造纸+农林、其余并进装修，得约 856 大卡，与 720 同量级，敏感性可接受。", size=11, space=6)
  p(doc, "装修占吨位 61%，但热只约占篮子 37%（约 441 大卡加权）。农林占吨 14%，热约占 34%。吨、热、钱不是同一套占比。综合数的区间传到装修会被放大——这是方法边界，不是数据错误。", size=11, space=6)
  p(doc, "天越、苏再投装修化验是 4438～5747 大卡（筛上干样、偏干）。入炉是湿料、灰分、筛下不可燃、坑内掺混之后的反平衡。造纸、农林化验可以当入炉用，装修化验当不了。差在装修状态，不在综合数 1188。", size=11, space=10)

  p(doc, "五、模型精度还能提多少：三个抓手", size=14, bold=True, space=8, before=8)
  table(doc, ["措施", "投入", "预期收益", "状态"], [
    ["① 装修入炉湿样化验 2～3 批", "约几百元/样", "装修从纯残差变为有独立锚；装修区间至少砍半", "建议尽快做"],
    ["② 垃圾池库存数据接入", "目测料位约 30 秒/天", "α 噪声再降；综合区间再收 1～2 个点", "方案已备，待启动"],
    ["③ DCS 小时数据（合格周）", "约 700 次请求，挂机一天", "炉侧计量更准，效率 η 更准", "接口已验证，待批量"],
  ])
  p(doc, "优先级：①最值（装修单独定价的直接依据）→ ②零成本 → ③锦上添花。三项全做后，综合区间有望从约 ±14% 收到 ±8%～10%，装修从「区间估计」升级为「有锚测算」。", size=11, space=10)

  p(doc, "六、定价模型接入", size=14, bold=True, space=8, before=8)
  p(doc, "综合数反推管「这一篮子值多少热」；定价引擎管「某一单收多少钱」。市场化定价引擎 V4.1 已按入炉反推数跑过一版，见 outputs/pricing/定价表_入炉反推_装修720.xlsx。草图，不是合同价。", size=11, space=8)

  p(doc, "6.1 厂内口径", size=12, bold=True, space=4)
  table(doc, ["项", "取值"], [
    ["入炉态", "反推已是入炉热值，堆放天数=0，不再打堆放折（k_stock=1）"],
    ["含水", "厂内 k_moist=1（检测低位已是该含水收到基）"],
    ["炉渣运出价", "30 元/吨渣（出厂卖价）"],
    ["炉渣率", "出厂 127,684 t / 入炉 514,380 t = 24.82%"],
    ["摊到每吨垃圾", "30 × 24.82% = 7.5 元/t（不是每吨垃圾进账 30）"],
    ["掺烧档", "按 20% 档取效率、厂用电与耗材单耗"],
    ["变动成本已含", "石灰、活性炭、氨水、天然气、飞灰、检修、渗滤液、除盐水"],
    ["未进变动成本", "折旧、运行人工、财务费用（吨固定成本约 47 元，全成本另算）"],
  ])
  p(doc, "掺烧 20% 单耗：石灰 27 kg/t、活性炭 0.68 kg/t、氨水 1.35 kg/t、天然气 0.28 m³/t（4.19 元/m³ → 约 1.17 元/t）。天然气占比很小。飞灰处置 40 元/t、检修 16 元/t 是变动成本大头。", size=11, space=8)

  p(doc, "6.2 单批次（处置费=0，变动成本层）", size=12, bold=True, space=4)
  table(doc, ["批次", "入炉大卡", "发电", "炉渣", "变动成本", "0 费边际", "盈亏平衡处置价 B"], [
    ["综合固废", "1188", "96.3", "7.5", "80.1", "+23.6", "−23.6"],
    ["装修", "720", "58.4", "7.5", "77.1", "−11.3", "+11.3"],
    ["造纸（理文）", "1626", "131.8", "7.5", "88.6", "+50.6", "−50.6"],
    ["农林（雷博尔）", "2792", "226.3", "7.5", "78.0", "+155.8", "−155.8"],
  ], highlight_last=False)
  p(doc, "B = 变动成本 − 发电 − 炉渣。B>0 表示没有处置费时变动成本层亏损。综合 0 费仍赚，是因为 1188 大卡的发电盖住了药剂；装修 720 不够热，药费几乎按吨走（飞灰+检修+石灰约 69 元），发电只有 58 元。打平大约 860～900 大卡。演示处置费 80 元时装修边际 +68.7，不代表 0 费也赚。", size=11, space=6)
  p(doc, "全成本另说：吨固定成本约 47 元。综合 0 费变动成本层 +24，扣固定后约 −24。会上若问「白收会不会亏」，要讲清：药剂电渣这层不亏；摊上折旧人工仍亏。", size=11, space=8)

  p(doc, "6.3 装修处置价分档（σ=15%）", size=12, bold=True, space=4)
  table(doc, ["处置价 元/t", "期望边际", "P5 边际", "亏损概率", "判定"], [
    ["0", "−11.3", "−25.7", "90%", "高风险"],
    ["10", "−1.3", "−15.7", "56%", "高风险"],
    ["20", "+8.7", "−5.7", "16%", "关注"],
    ["30", "+18.7", "+4.3", "1.6%", "安全"],
    ["80", "+68.7", "+54.3", "约 0", "安全（演示价）"],
  ])
  p(doc, "变动成本层，装修报价不要低于 30 元/t（σ=15% 下亏损概率才落到 2% 以内）。20 元期望还能赚，尾部仍可能亏。湿样化验到位后，可用该批入炉湿样替换 720 残差，分档更稳。", size=11, space=8)

  p(doc, "6.4 敏感性：农林若只有检测一半（2792→1396）", size=12, bold=True, space=4)
  p(doc, "锅炉反推的综合 1188 不一定动——那是蒸汽拧出来的整篮热。化验砍半，改的是热记在谁账上。", size=11, space=4)
  table(doc, ["口径", "综合 0 费边际", "装修", "农林"], [
    ["A 篮子总热不变，热还给装修", "仍约 +24", "残差升至约 1047 大卡，0 费转正约 +15", "仍 +43（1396 高于打平线）"],
    ["B 成分加权、总热跟着农林降", "综合落到约 987 大卡，0 费约 +7～8", "仍按 720，0 费仍亏 11", "仍 +43"],
  ])
  p(doc, "农林砍一半，它自己 0 费仍赚。真正被农林化验绑架的是装修。此段是假设，雷博尔报告仍是 2792。", size=11, space=10)

  p(doc, "七、锁定与使用注意", size=14, bold=True, space=8, before=8)
  p(doc, "锁定：综合 1188 大卡；装修入炉约 720 大卡。", size=12, bold=True, space=8)
  for line in [
    "不要说「综合 1188 大卡，所以装修也是 1188」。",
    "不要用装修化验 4000 多大卡否定入炉约 720 大卡。",
    "不要和 2025 年回归数直接比年（当年装修更重、无克劳丽，且内部验证未过，不能报价）。",
    "不要把综合 0 费不亏理解成装修 0 费也不亏；也不要理解成全成本不亏。",
    "炉渣按 30 元/吨渣运出价，摊到垃圾是 7.5 元/t，不要按每吨垃圾进账 30。",
    "定价表是变动成本草图，未扣折旧人工财务；不是商务报价单。",
    "收到基低位、入炉态。不是干基，不是单车化验。",
  ]:
    p(doc, "·  " + line, size=11, space=3)

  p(doc, "测算：固废智能运营台      日期：2026-09-03", size=10, color=MUTED, space=0, before=12)
  p(doc, "附件：定价表_入炉反推_装修720.xlsx  ·  幻灯片模版 固废热值测算_20260903.pptx",
    size=10, color=MUTED, space=0)

  fp = doc.sections[0].footer.paragraphs[0]
  fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
  r = fp.add_run("固废热值测算报告  ·  综合 1188 大卡  ·  装修入炉约 720 大卡  ·  不含克劳丽")
  font(r, size=8, color=MUTED)
  doc.save(DOCX)
  print("word", DOCX)


if __name__ == "__main__":
  build()
