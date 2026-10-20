"""Bar charts for the biosimilar market slide (style matched to the Bio RA RAG deck)."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import koreanize_matplotlib  # noqa: F401  (registers NanumGothic)

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

NAVY = "#1b3a57"
TEAL = "#14a5bb"
GRAY = "#c5d0da"
MUTED = "#5b6876"
GRID = "#e3e9ef"

fdir = os.path.join(os.path.dirname(koreanize_matplotlib.__file__), "fonts")
for f in os.listdir(fdir):
    font_manager.fontManager.addfont(os.path.join(fdir, f))
plt.rcParams.update({
    "font.family": "NanumGothic",
    "axes.unicode_minus": False,
    "text.color": NAVY,
    "axes.labelcolor": NAVY,
    "xtick.color": NAVY,
    "ytick.color": NAVY,
    "axes.edgecolor": GRID,
    "figure.dpi": 100,
})


def finish(fig, ax, name, title, source):
    fig.text(0.012, 0.975, title, fontsize=16, fontweight="bold", color=NAVY, ha="left", va="top")
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    fig.text(0.012, 0.015, "출처: " + source, fontsize=9, color=MUTED, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.05, 1, 0.9))
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), dpi=220, transparent=True)
    fig.savefig(os.path.join(OUT, f"{name}_white.png"), dpi=220, facecolor="white")
    plt.close(fig)


def label_bars(ax, bars, fmt, color=NAVY, inside=False, size=12):
    for b in bars:
        v = b.get_height()
        ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, v), xytext=(0, 5),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=size, fontweight="bold", color=color)


# 1. 스텔라라 매출: 세계 vs 미국 (억 달러)
years = ["2023", "2024", "2025"]
world = [108.6, 103.6, 61]
us = [70, 67, 38]
fig, ax = plt.subplots(figsize=(9, 5))
x = range(len(years))
w = 0.36
b1 = ax.bar([i - w / 2 for i in x], world, w, color=NAVY, label="세계 매출")
b2 = ax.bar([i + w / 2 for i in x], us, w, color=TEAL, label="미국 매출")
label_bars(ax, b1, lambda v: f"{v:g}억")
label_bars(ax, b2, lambda v: f"{v:g}억", color=TEAL)
ax.set_xticks(list(x), ["2023", "2024\n유럽 바이오시밀러 출시(7월)", "2025\n미국 바이오시밀러 출시(1월)"], fontsize=12.5)
ax.set_ylim(0, 135)
ax.set_yticks([])
ax.legend(frameon=False, loc="upper right", fontsize=11, ncol=2)
finish(fig, ax, "01_stelara_sales",
       "스텔라라 매출: 2024→2025년 세계 매출 약 41% 감소 (단위: 달러)",
       "J&J 10-K FY2024·FY2025, Evaluate/Pharm Exec(2023), BioSpace(2025 세계 약 61억). 2025 세계 매출은 반올림 값")

# 2. 우스테키누맙 바이오시밀러 누적 허가 수 (반기별)
periods = ["2023\n하반기", "2024\n상반기", "2024\n하반기", "2025\n상반기", "2025\n하반기"]
eu = [0, 3, 7, 8, 11]
usc = [1, 3, 7, 8, 8]
fig, ax = plt.subplots(figsize=(9, 5))
x = range(len(periods))
b1 = ax.bar([i - w / 2 for i in x], eu, w, color=NAVY, label="유럽(EMA)")
b2 = ax.bar([i + w / 2 for i in x], usc, w, color=TEAL, label="미국(FDA)")
label_bars(ax, b1, lambda v: f"{v:g}" if v else "0")
label_bars(ax, b2, lambda v: f"{v:g}", color=TEAL)
ax.set_xticks(list(x), periods, fontsize=12)
ax.set_ylim(0, 13.5)
ax.set_yticks([])
ax.legend(frameon=False, loc="upper left", fontsize=11, ncol=2)
finish(fig, ax, "02_ustekinumab_cumulative",
       "우스테키누맙 바이오시밀러 누적 허가 수: 2년 만에 유럽 11개·미국 8개",
       "EMA 허가 목록(2026-10-07 조회), FDA Purple Book 2026-09판, 업계 보도(Selarsdi·Otulfi·Imuldosa 허가 월)")

# 3. EU 허가 바이오시밀러 수, 성분별 (상위 11개)
ing = [("데노수맙", 30), ("애플리버셉트", 12), ("우스테키누맙", 11), ("페그필그라스팀", 10),
       ("아달리무맙", 10), ("라니비주맙", 8), ("트라스투주맙", 8), ("베바시주맙", 8),
       ("필그라스팀", 7), ("리툭시맙", 6), ("테리파라타이드", 6)]
ing = ing[::-1]
fig, ax = plt.subplots(figsize=(9, 5.6))
cols = [TEAL if n == "우스테키누맙" else NAVY for n, _ in ing]
bars = ax.barh([n for n, _ in ing], [v for _, v in ing], color=cols, height=0.62)
for b, (n, v) in zip(bars, ing):
    ax.annotate(f"{v}", (v, b.get_y() + b.get_height() / 2), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=12, fontweight="bold", color=TEAL if n == "우스테키누맙" else NAVY)
ax.set_xlim(0, 34)
ax.set_xticks([])
ax.tick_params(axis="y", labelsize=12)
ax.spines["bottom"].set_visible(False)
ax.text(33.5, 0.2, "EU 허가 바이오시밀러 151개 · 27개 성분\n상위 11개 성분이 116개(77%)\n그 외 16개 성분 35개",
        ha="right", va="bottom", fontsize=11, color=MUTED, linespacing=1.6)
finish(fig, ax, "03_eu_biosimilars_by_ingredient",
       "EU 허가 바이오시밀러 수, 성분별: 한 성분에 제품이 몰린다",
       "EMA 허가 의약품 목록(2026-10-07 조회, 허가명 기준). 같은 제품의 두 허가명도 각각 집계")

# 4. 미국 2025–2034 독점 상실 바이오의약품 vs 바이오시밀러 개발 중
fig, ax = plt.subplots(figsize=(9, 4))
labels = ["바이오시밀러\n개발 중", "독점을 잃는\n바이오의약품"]
vals = [12, 118]
bars = ax.barh(labels, vals, color=[TEAL, NAVY], height=0.5)
for b, v, t in zip(bars, vals, ["12개 (약 10%)", "118개 · 매출 2,340억 달러"]):
    ax.annotate(t, (v, b.get_y() + b.get_height() / 2), xytext=(8, 0), textcoords="offset points",
                va="center", fontsize=13, fontweight="bold", color=TEAL if v == 12 else NAVY)
ax.set_xlim(0, 175)
ax.set_xticks([])
ax.tick_params(axis="y", labelsize=12.5)
ax.spines["bottom"].set_visible(False)
finish(fig, ax, "04_us_loe_2025_2034",
       "미국 2025–2034년: 독점을 잃는 바이오의약품 118개, 바이오시밀러 개발은 12개뿐",
       "IQVIA Institute, Assessing the Biosimilar Void in the U.S. (2025-02). 개발 현황은 2024년 기준")

# 5. 글로벌 2025–2030 특허 만료
fig, ax = plt.subplots(figsize=(9, 4))
labels = ["그중 블록버스터\n(연매출 10억 달러↑)", "특허 만료 의약품"]
vals = [70, 200]
bars = ax.barh(labels, vals, color=[TEAL, NAVY], height=0.5)
for b, v, t in zip(bars, vals, ["70여 개", "약 200개 · 영향 매출 2,000억~4,000억 달러"]):
    ax.annotate(t, (v, b.get_y() + b.get_height() / 2), xytext=(8, 0), textcoords="offset points",
                va="center", fontsize=13, fontweight="bold", color=TEAL if v == 70 else NAVY)
ax.set_xlim(0, 400)
ax.set_xticks([])
ax.tick_params(axis="y", labelsize=12.5)
ax.spines["bottom"].set_visible(False)
finish(fig, ax, "05_global_patent_2025_2030",
       "글로벌 2025–2030년: 특허 만료 약 200개, 블록버스터 70여 개",
       "한국바이오협회 바이오경제연구센터 「특허 절벽: 미국 바이오시밀러 시장을 공략하라」(2025-12) 보도")
print("done")
