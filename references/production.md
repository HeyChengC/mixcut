# 从题目到交片：命令、审片、样片前自查

做片时按站往下走。下面 `py` = `python -I`，`$S` = skill 的 `scripts` 目录，命令都在作品目录里跑。

## 0 开工

```
works\<YYYY-MM-DD>_<主题>\
├── brief.md      立意、立论句、段落表、素材表、请用户确认的事、踩的坑
├── source\       build.py、edl.json（永久保留）
├── assets\       src\ proxy\ music\（发布后可以删）
├── renders\      预览和中间版本（发布后可以删）
└── final\        成片、总览图（永久保留）
```

作品名按 STUDIO 的规矩：`日期_主题`，例如 `2026-11-02_友谊混剪`。

## 1 命令，按站

**站 2 素材**

```
py $S\fetch.py search "<片名> official trailer" --n 8
py $S\fetch.py get <id> <id> --dir assets/proxy --h 360
py $S\fetch.py get <id> --dir assets/src --h 1080
py $S\fetch.py music "<曲名 作者>" --dir assets/music
py $S\music.py analyze assets/music/<曲子>.m4a
py $S\lines.py assets/proxy/*.mp4 assets/src/*.mp4 --sep
py $S\shots.py log assets/proxy/*.mp4 assets/src/*.mp4
```

**站 3 段落表**

```
py $S\music.py window assets/music/<曲子>.m4a --len 45        # 推荐截哪几段（起点在小节头、里面有落拍）
py $S\music.py seams assets/music/<曲子>.m4a --out 50-58 --in 68-80
py $S\shots.py peek assets/proxy/a.mp4@63.2+2 assets/src/b.mp4@10 --out peek.jpg
```

**站 4 剪辑表**

```
py source\build.py                      # 生成 source\edl.json，打印每段起点和总长
py $S\edl_check.py source\edl.json      # 0 问题才往下
```

**站 5 预览**

```
py $S\render.py source\edl.json --still 8.4 --still 22.5     # 先出几张关键帧看字和构图
py $S\render.py source\edl.json --preview                    # -> renders\edl_preview.mp4
py $S\qc.py renders\edl_preview.mp4                          # -> .cuts.jpg .strip.jpg
py $S\small.py renders\edl_preview.mp4.cuts.jpg --w 1000     # 看图前先缩
py $S\fsheet.py renders\edl_preview.mp4 --from 9.0 --to 9.8 --step 0.0417 --out x.jpg   # 逐帧看一处转场
```

**站 6 成片**

```
py $S\fetch.py conform source\edl.json          # YouTube 要登录时停下来问用户（footage.md 第 3 节）
py $S\render.py source\edl.json                 # -> renders\edl.mp4
py $S\qc.py renders\edl.mp4
```

成片复制到 `final\<片名>_混剪_vN.mp4`，`cuts.jpg` 复制成 `final\<片名>_混剪_vN_总览.jpg`。

## 2 审片：交给子代理，至少两轮

用户说过："效果你必须反复自己迭代好几次再说。"

- 给子代理：预览 mp4、`timeline.json`、`cuts.jpg`、`strip.jpg`、段落表、build 脚本，以及这份 skill 的 `picture.md`、`arc.md`、`type.md`、`transitions.md`。让它逐镜看，按 P0（不改不能交）/ P1（这轮要改）/ P2（可以以后改）列问题，每条写时间、镜号、问题、改法；换镜头的建议要按 4:3 裁切模拟过（`shots.py peek`），没模拟过的写明。
- 看图都缩到 1000 宽以内，一次一张。
- 它给的 focus、入点、亮度建议要自己核一下（v4 第二轮有一条 focus 给反了，实测才知道）。
- 改完再渲一遍、再审一轮。第二轮只剩小问题再进第 6 站。

## 3 样片前自查

每条片在交预览、交成片前都跑一遍。用户每指出一个新问题，在这里加一行。

| # | 查什么 | 怎么查 | 过关线 |
|---|---|---|---|
| 1 | 立论句 | 段落表 | 开场 10 秒内说完；结尾有兑现它的那一镜 |
| 2 | 台词链 | 只读台词表，不看画面 | 一句比一句重，能接上；念一遍能听出起承转落 |
| 3 | 落拍第一刀 | `timeline.json` 的切点 vs `beats.json` 的落拍 | 误差 ≤1 帧 |
| 4 | 节奏的数 | `timeline.json` | 每 10 秒 ≥10 刀；中位数 ≤0.55 秒；超过 2 秒的镜头只在立论、急停、落点 |
| 5 | 急停 | `timeline.json` | 最快那串之后有一个 ≥1.2 秒的特写 |
| 6 | 最重的那句 | 台词表对 `timeline.json` | 不在最快的切里；一句一镜，先脸后声 |
| 7 | 每镜有人 | `cuts.jpg` | 没有黑屏、背影、空镜（结尾象征物除外）、看不清的脸、跑错人、时间倒退 |
| 8 | 亮度 | `cuts.jpg` | 最快那段不是全片最暗；没有手机上看是黑屏的镜头 |
| 9 | 画面干净 | `cuts.jpg` 的边和角 | 没有黑边、水印、台标、原片字幕 |
| 10 | 镜头越界 | `edl_check.py` | 0 问题 |
| 11 | 同机位硬切 | 相邻两格并排看 | 构图一样的两镜不挨着（像卡了一帧） |
| 12 | 字不压脸 | `--still` 出字的那几帧 | 第一张脸、片名下的脸、高潮那张脸都没被字压住 |
| 13 | 字读得完 | 字数 ÷ 停留秒数 | 每秒不超过 5 个汉字 |
| 14 | 字够大 | texts 和 sub_scale | 1080 高时字幕中文 ≥46 像素、英文 ≥30 像素；最重那段的字不是全片最小 |
| 15 | 特效只在门上 | `timeline.json` 的 fx 列 | 每处特效都在落拍、段落交接、进高潮或高潮那串上；每种不超过 3 次（高潮连甩算一次） |
| 16 | 台词和音乐 | 渲染时打印的每句差值 | +8 到 +10 dB（音乐停了的地方除外） |
| 17 | 响度 | `qc.py` | 约 -14 LUFS，真峰值 ≤ -1 dBTP |
| 18 | 接缝 | `music.py seams` 的分 | harm ≥0.9，或者是故意的对比切（配了转场） |
| 19 | 首尾 | 第一镜和最后一镜并排 | 同一个人、同一个构图（可以倒过来），或兑现开头那句的画面 |
| 20 | 最后一镜 | `timeline.json` | 停 1.5–4 秒，没特效，和音乐一起收 |
| 21 | 高清 | `edl_check.py` / conform 的输出 | 还在用 360p 代理的镜头逐个列进交付说明 |
| 22 | 帧率 | 素材的 `meta.json` | 素材 23.976 fps 时成片写 24 |

## 4 交付

- `final\` 里：成片、总览图。
- `brief.md` 里写这几节：
  - 立意、立论句（带出处）；
  - 段落表（时间、讲什么、台词、用了曲子哪一段、怎么剪）；
  - 字：选了什么字体、为什么（一句话）；
  - **按推荐做了、请用户确认的**（立论句、结构、结尾构图、改过的台词、垫底的音乐……）；
  - 偏软的镜头（还在用代理的）；
  - **没听过的**：每个接缝、每句借来的声音、喊声配哪张脸；
  - 和参考片对过的数（第 3 节第 4 行）。
- 把成片路径和要用户确认的事交给用户。
