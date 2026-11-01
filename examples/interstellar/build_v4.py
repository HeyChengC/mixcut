# Snapshot (2026-10-31) of the build script of the Interstellar test film.
# The footage is not in the skill, so it does not run from here. Read it for the pattern: clip(), and how
# say / say_src / line / words / big become dialog and texts. Do not copy the shots.
"""Build edl_v4.json: the Interstellar mixcut rebuilt from the cutting logic of the references (ref_notes.md, "剪辑逻辑").
One sentence first, then pictures; the shape is slow - build - drop - fast - stop - slow - slowest. Run: python build_v4.py

Thesis: "Once you're a parent, you're the ghost of your children's future." Planted in the cold open (he sits by her bed),
paid off after the climax by Murph herself: "It was you. You were my ghost." Ends on the same bedside, roles reversed.

Timeline beats (b0 = song b70; 1 beat = 0.469 s):
  S1 b0-b20   cold open     song b70-b90   (33.198-42.593)  quiet build; three lines; title; first cut of S2 on the drop
  S2 b20-b48  leaving       song b90-b118  (-> 55.697)      one cut a beat, one half-beat burst, then a two-beat stop
  S3 b48-b64  time          song b150-b166 (70.686-78.184)  seam b118->b150 harm 0.98; white flash, colder
  S4 b64-b88  the climax    song b230-b254 (108.201-119.465) seam b166->b230 harm 0.99, +4.6 dB; cuts get shorter,
                                                            then stop on STAY
  S5 b88-end  he came back  song stops dead at b254 (decays ~3 s); then the song's quiet organ intro fades in under
                            the last lines. Shots here are timed in seconds, one line one shot.
"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import render

P = "../assets/proxy/"
S = "../assets/src/"
SRC = dict(BYE=P + "yMpdgnUYVNo.mp4", TES=P + "Rvns5DaW-ug.mp4", END=P + "ECjYsWLgy3I.mp4", MSG=P + "xGrbLfi1VJ8.mp4",
           WAVE=P + "TF2nIlCs9Qg.mp4", DOCK=P + "v2H1s9gj5DA.mp4", LAUNCH=P + "x9xFRch7N5o.mp4",
           T2=S + "Lm8p5rlrSkY_m3u8.mp4", UK=S + "zSWdZVtXT7E.mp4", T1=S + "2LqzF5WauAw.mp4", TZ=S + "nyc6RJEEe0U.mp4",
           DG=S + "3_nprPycZow.mp4")
CREAM = [246, 238, 222, 250]
COLD = [0.9, 0.98, 1.1]


def clip(src, t_in, beats, note="", **kw):
    path = src if src.startswith("../") else SRC[src]
    if beats is not None:
        kw["beats"] = beats
    return dict(src=path, **{"in": t_in}, note=note, **kw)


# ------------------------------------------------------------------ S1 cold open (b0-b20): three long shots, cut in the breaths
S1 = [
    clip("BYE", 44.00, 7, "立论：他坐在她床边，手搭着熟睡的她", tint=[1.2] * 3, duck_db=-3, gain_db=5, say=[44.30, 47.12], big="thesis"),
    clip("BYE", 165.40, 5, "她不信", focus=[0.47, 0.45], duck_db=-3, say=[165.70, 167.85], big="noidea"),
    clip("BYE", 195.95, 8, "他答应（同期口型）；片名压在这一镜上", duck_db=-3, say=[196.45, 197.60], big="promise"),
]

# ------------------------------------------------------------------ S2 leaving (b20-b48): the music talks
S2 = [
    clip("T2", 69.11, 1, "点火，第一刀卡死在落拍上", fx="flash_blur"),
    clip("BYE", 275.20, 1, "她跑出门廊", focus=[0.46, 0.387]),
    clip("LAUNCH", 135.20, 1, "他在座舱里", focus=[0.49, 0.38]),
    clip("UK", 80.10, 1, "她被外公拽住", focus=[0.45, 0.45], zoom=1.3),
    clip("T2", 71.03, 1, "助推器"),
    clip("T2", 66.60, 2, "他开车，忍着（1080p 版）", focus=[0.53, 0.374], zoom=1.25, tint=[1.2] * 3),
    clip("BYE", 172.00, 1, "地上的手表（埋给高潮）"),
    clip("T2", 76.50, 1, "拥抱（回忆）"),
    clip("BYE", 272.40, 1, "他开车回头", focus=[0.47, 0.42]),
    clip("T2", 72.84, 0.5, "过载", focus=[0.45, 0.448]),
    clip("T2", 72.09, 0.5, "火箭"),
    clip("LAUNCH", 113.00, 0.5, "过载（另一镜，反光少）"),
    clip("T2", 74.20, 0.5, "云上"),
    clip("BYE", 284.00, 3, "停：她哭（多给一拍）", focus=[0.5, 0.424]),
    clip("DG", 1.00, 1, "空间站", fx="whip_in"),
    clip("T2", 86.75, 1, "布兰德"),
    clip("T2", 90.32, 1, "他在虫洞里", focus=[0.454, 0.36], zoom=1.1, tint=[1.6] * 3),
    clip("T2", 91.30, 1, "穿过虫洞"),
    clip("WAVE", 97.30, 1, "巨浪上的飞船", fx="whip_in"),
    clip("WAVE", 103.30, 1, "浪墙"),
    clip("WAVE", 62.70, 2, "库珀喊（头盔特写）", focus=[0.523, 0.375]),
    clip("T2", 125.42, 1, "库珀喊（近）", focus=[0.66, 0.293]),
    clip("T2", 118.30, 1, "浪里的飞船"),
    clip("WAVE", 110.42, 1, "浪拍下来"),
    clip("WAVE", 78.85, 2, "他大喊（白闪前最后一张脸）", focus=[0.5, 0.4]),
]

# ------------------------------------------------------------------ S3 time (b48-b64): white flash, colder; a line over 4 cuts, then the split
S3 = [
    clip("MSG", 64.60, 2, "回到飞船，他一脸茫然", fx="flash", tint=COLD, focus=[0.472, 0.44],
         line=dict(src="MSG", a=17.50, b=20.75, at=0.25, duck_db=-10), words="years"),
    clip("MSG", 52.50, 2, "罗米利老了 23 年", tint=COLD, focus=[0.472, 0.341]),
    clip("MSG", 71.70, 2, "布兰德掉泪", tint=COLD, focus=[0.522, 0.44]),
    clip("MSG", 300.00, 2, "屏幕里墨菲长大了（“8 天”落在这一镜）", tint=COLD, zoom=1.18, focus=[0.46, 0.50]),
    clip("MSG", 256.75, 2, "他的脸，泪眼", tint=COLD, focus=[0.522, 0.372]),
    clip("MSG", 304.70, 6, "分屏：左他哭（慢放），右她从暗里淡入，喊他回来；右边 314.05 起手挡脸，所以入点提前、声音晚 0.2 秒", speed=0.75, tint=COLD,
         focus=[0.472, 0.387],
         layout=dict(type="split", b=dict(src=SRC["MSG"], **{"in": 311.18}, tint=COLD, focus=[0.522, 0.409]), b_k=6, b_fade=6, anim=False, gap=4),
         duck_db=-10, say=[311.50, 313.95], say_src="MSG", say_at=0.32, words="comeback"),
]

# ------------------------------------------------------------------ S4 climax (b64-b88): 2-beat shots, then 1, then half with whips, then the stop
S4 = [
    clip("T2", 141.20, 2, "卡冈图雅（进高潮的门）", fx="rays", focus=[0.53, 0.5]),
    clip("T2", 139.20, 2, "他的眼睛，盯着黑洞", focus=[0.47, 0.30], zoom=1.45),
    clip("T2", 129.31, 1, "被甩出去", fx="whip_in_d", focus=[0.65, 0.463]),
    clip("TES", 284.30, 2, "往下落", focus=[0.63, 0.28], focus_end=[0.48, 0.33], zoom=1.35),
    clip("TES", 289.70, 2, "漂浮大特写，张嘴", focus=[0.6, 0.364]),
    clip("TES", 68.30, 1, "书架后，他的脸", focus=[0.6, 0.477], tint=[1.3] * 3),
    clip("TES", 4.00, 0.5, "书掉下来", tint=[1.3] * 3),
    clip("TES", 30.30, 1.5, "长大的墨菲抬眼，想通了", focus=[0.62, 0.36], tint=[1.25] * 3),
    clip("TES", 74.30, 1, "他的侧脸", focus=[0.52, 0.44], tint=[1.25] * 3),
    clip("TES", 457.00, 1, "架上的手表", focus=[0.5, 0.576]),
    clip("TES", 445.50, 0.5, "手指碰秒针", focus=[0.5, 0.424], tint=[1.3] * 3),
    clip("TES", 468.20, 1.5, "秒针跳（入点带上第一跳）", focus=[0.6, 0.5]),
    clip("TES", 466.00, 1, "长大的墨菲看着表（高清）", tint=[1.25] * 3),
    clip("TES", 10.25, 0.5, "书架后", fx="whip_in_l", focus=[0.62, 0.62], zoom=1.3, tint=[1.25] * 3),
    clip("TES", 469.15, 0.5, "秒针又跳", fx="whip_in", focus=[0.6, 0.5]),
    clip("TES", 471.50, 0.5, "她开口", fx="whip_in_l", focus=[0.59, 0.349], tint=[1.25] * 3),
    clip("TES", 291.00, 0.5, "手套冲向镜头", fx="whip_in", focus=[0.68, 0.349]),
    clip("TES", 437.00, 0.5, "她笑了", fx="whip_in_l", focus=[0.717, 0.311], tint=[1.25] * 3),
    clip("TES", 102.97, 0.5, "他喊“不”", fx="whip_in", focus=[0.50, 0.24], zoom=1.3, tint=[1.25] * 3),
    clip("TES", 344.90, 4, "停：大特写，他喊 STAY（画面不是书架那场；这段张大嘴、没台词；喊声从 3.40 那遍借来）", shade=[0.55, 0.7], duck_db=-13, gain_db=4,
         say=[3.40, 4.55], say_src="TES", words="stay", fade_out=0.12, say_at=0.30, focus=[0.57, 0.417]),
]

# ------------------------------------------------------------------ S5 he came back (b88-end): the music has stopped; one line, one shot
S5 = [
    clip("TES", 124.60, None, "长大的墨菲：是你（真空口，先给脸）", dur=2.60, zoom=1.2, duck_db=-4, say=[125.40, 126.90], zh="是你。", en="It was you."),
    clip("TES", 138.55, None, "她在书架前：你就是我的鬼魂（回答开头那句，字和开头一样大）", dur=2.10, duck_db=-2,
         say=[138.85, 140.35], big="ghost", zoom=1.25, focus=[0.30, 0.38], tint=[1.5] * 3),
    clip("END", 68.75, None, "老墨菲", dur=2.35, duck_db=-2, gain_db=11, say=[69.05, 70.80], zh="但我知道你会回来", en="but I knew you'd come back."),
    clip("END", 76.59, None, "她说出答案（源 78.54 换镜，不能越过）", dur=1.88, focus=[0.58, 0.5], duck_db=-2, gain_db=4, say=[76.95, 79.65],
         zh="因为我爸答应过我", en="Because my dad promised me.", sub_x=0.36),
    clip("END", 78.56, None, "落点：他坐在她床边，握着她的手（和开头同一个构图，倒过来）", dur=3.90, zoom=[1.0, 1.0]),
]

SECTIONS = (S1, S2, S3, S4, S5)
PRIVATE = ("say_src", "words", "line", "big", "say_at")


def build():
    edl = {
        "title": "星际穿越 · 你是我的鬼魂（v4）",
        "idea": "父亲答应女儿会回来，这句话穿过了几十年。当了父母，你就成了孩子未来的鬼魂。",
        "size": [1440, 1080], "fps": 24,
        "music": {"file": "../assets/music/cornfield_chase.m4a", "fade_in": 0.0, "fade_out": 1.2, "gain_db": 0, "xfade": 0.05,
                  "segments": [[33.198, 55.697], [70.686, 78.184], [108.201, 122.751], {"from": 3.0, "to": None, "xfade": 1.5}]},
        "look": {"contrast": 1.06, "saturation": 0.98, "grain": 0.02, "vignette": 0.25, "normalize": 0.25, "sharpen": 0.2},
        "sub_style": "serif", "sub_scale": 1.3, "end_fade": 0.9,
        "clips": [], "dialog": [], "texts": [],
    }
    for sec in SECTIONS:
        for c in sec:
            edl["clips"].append({k: v for k, v in c.items() if k not in PRIVATE})
            c["_i"] = len(edl["clips"]) - 1
    grid, pieces = render.music_grid(edl, HERE)
    tl = render.layout_clips(edl, grid)
    texts, dialog = edl["texts"], edl["dialog"]
    for sec in SECTIONS:
        for c in sec:
            i = c["_i"]
            t0 = tl[i]["t0"]
            if c.get("say_src"):            # the voice comes from another shot than the picture under it
                e = edl["clips"][i]
                a, b = e.pop("say")
                d = dict(src=SRC[c["say_src"]], **{"in": a, "out": b}, at=round(t0 + c.get("say_at", 0.02), 3),
                         duck_db=e.pop("duck_db", -6), gain_db=e.pop("gain_db", 0))
                if c.get("fade_out"):
                    d["fade_out"] = c["fade_out"]
                dialog.append(d)
                c["_line_at"], c["_line_a"] = d["at"], a
            elif c.get("say"):
                c["_line_at"], c["_line_a"] = t0 + (c["say"][0] - c["in"]) / c.get("speed", 1), c["say"][0]
            if c.get("line"):
                L = c["line"]
                at = t0 + float(L.get("at", 0))
                dialog.append(dict(src=SRC[L["src"]], **{"in": L["a"], "out": L["b"]}, at=round(at, 3), duck_db=L.get("duck_db", -6)))
                c["_line_at"], c["_line_a"] = at, L["a"]
            if c.get("words"):
                words_for(c["words"], c, tl, i, texts)
            if c.get("big"):
                big_for(c["big"], c, tl, i, texts)
    # title over the last shot of the cold open: in after "I'm coming back", gone with the drop (ref D 9.75 s)
    t_drop = tl[len(S1)]["t0"]
    texts.append(dict(at=round(t_drop - 1.75, 3), dur=1.75, text="INTERSTELLAR", style="moment", kind="pf_roman", size=72,
                      tracking=0.42, y=0.80, fill=CREAM, fade=[0.45, 0.12], shadow=0.9))
    texts.append(dict(at=round(t_drop - 1.55, 3), dur=1.55, text="星际穿越", style="moment", kind="zh_serif", weight=600, size=36,
                      tracking=0.6, y=0.875, fill=CREAM, fade=[0.45, 0.12], shadow=0.9))
    end = tl[-1]["t1"]
    s5 = tl[len(S1) + len(S2) + len(S3) + len(S4)]["t0"]
    print(f"S2 {tl[len(S1)]['t0']:.2f}  S3 {tl[len(S1) + len(S2)]['t0']:.2f}  S4 {tl[len(S1) + len(S2) + len(S3)]['t0']:.2f}  "
          f"S5 {s5:.2f} (b88 = {grid.resolve('b88'):.2f})  end {end:.2f}")
    out = HERE / "edl_v4.json"
    out.write_text(json.dumps(edl, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out, f"{len(edl['clips'])} clips, {end:.2f}s")


def word(texts, t, until, text, x, y, size, kind="pf_italic", anchor="l", **kw):
    texts.append(dict(at=round(t, 3), dur=round(until - t, 3), text=text, style="moment", kind=kind, size=size, x=x, y=y,
                      anchor=anchor, tracking=kw.pop("tracking", 0.0), fill=kw.pop("fill", CREAM), shadow=kw.pop("shadow", 0.45),
                      fade=kw.pop("fade", [0.07, 0.2]), **kw))


def big_for(kind, c, tl, i, texts):
    """The cold open's three lines, set large (ref D: the opening line is the biggest text in the film), each phrase
    appearing as it is said; Chinese serif big, the English small under it."""
    at, a = c["_line_at"], c["_line_a"]
    on = lambda src_t: at + (src_t - a)
    zh = dict(kind="zh_serif", weight=500, tracking=0.12, anchor="c")
    en = dict(kind="pf_italic", tracking=0.02, anchor="c", fill=[236, 232, 224, 225])
    if kind == "thesis":
        until = tl[i]["t1"] - 0.03
        word(texts, on(44.30), until, "当了父母，", 0.45, 0.17, 72, **zh)          # up in the dark, off her face
        word(texts, on(45.44), until, "你就成了孩子未来的鬼魂。", 0.45, 0.26, 72, **zh)
        word(texts, on(45.44), until, "Once you're a parent, you're the ghost of your children's future.", 0.45, 0.33, 32, **en)
    elif kind == "noidea":
        until = tl[i]["t1"]
        word(texts, on(165.70), until, "你根本不知道自己什么时候能回来。", 0.5, 0.80, 52, **zh)
        word(texts, on(165.70), until, "You have no idea when you're coming back.", 0.5, 0.87, 32, shadow=0.7, **en)
    elif kind == "promise":
        until = on(197.60) + 0.30                  # clears before the title takes its place
        word(texts, on(196.50), until, "我会回来的。", 0.5, 0.80, 58, **zh)
        word(texts, on(196.50), until, "And I'm coming back.", 0.5, 0.87, 32, **en)
    elif kind == "ghost":                          # the answer to the opening line, set the same size
        until = tl[i]["t1"]
        word(texts, on(138.85), until, "你就是我的鬼魂。", 0.5, 0.87, 58, **zh)       # low, under her chin
        word(texts, on(138.85), until, "You were my ghost.", 0.5, 0.935, 32, **en)


def words_for(kind, c, tl, i, texts):
    """Words that land on the voice (ref D): each word appears when it is said, sizes far apart, set as one group."""
    at, a = c["_line_at"], c["_line_a"]
    on = lambda src_t: at + (src_t - a)
    if kind == "years":                        # "It's 23 years, four months, eight days."
        until = tl[i + 3]["t1"]
        word(texts, on(17.80), until, "23 年", 0.10, 0.62, 96, kind="zh_serif", weight=500)
        word(texts, on(19.62), until, "4 个月", 0.10, 0.745, 50, kind="zh_serif", weight=400, tracking=0.08)
        word(texts, on(20.24), until, "8 天", 0.10, 0.835, 50, kind="zh_serif", weight=400, tracking=0.08)
    elif kind == "comeback":                   # "...for you to come back." across the split
        until = tl[i]["t1"]
        word(texts, on(313.41), until, "come back.", 0.75, 0.79, 84, kind="pf_italic_med", anchor="c")
        word(texts, on(311.57), until, "现在，正是你回来的好时候", 0.75, 0.88, 40, kind="zh_serif", weight=500, anchor="c", tracking=0.1)
    elif kind == "stay":                       # the word on the bookshelf; the shot holds after it
        until = tl[i]["t1"]
        word(texts, on(3.46), until, "STAY", 0.5, 0.79, 150, kind="pf_roman", anchor="c", tracking=0.32)
        word(texts, on(3.50), until, "留下", 0.5, 0.90, 60, kind="zh_serif", weight=500, anchor="c", tracking=0.4)


if __name__ == "__main__":
    build()
