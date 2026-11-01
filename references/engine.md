# 引擎参考：剪辑表 edl.json 怎么写

写 build 脚本、拿不准某个字段怎么写、写了会怎样时查这里。字段、单位、默认值都照 `scripts\render.py`、`fx.py`、`mc_common.py` 的代码写（2026-10-31）；这里没列的字段代码不读。下文 `py` = `python -I`，`$S` = skill 的 `scripts` 目录。

## 1. 文件放哪，怎么渲染

```
works\<日期_主题>\
├── source\           build.py、edl.json，渲染时还会写 .focus_cache.json（自动对焦的缓存）
│   └── assets\hi\    fetch.py conform 拉的高清片段（它放在"EDL 所在目录\assets\hi"，所以落在 source 底下）
├── assets\src\       直接下的素材（fetch.py get 默认 1080p）
├── assets\proxy\     长素材的 360p 代理：先拿它选镜头，定稿后 conform 补高清
├── assets\music\     配乐，旁边是 <文件>.beats.json（music.py analyze 出的拍子）
├── renders\          render.py 默认输出到这里
└── final\            成片
```

- 每个素材旁边有一串同名附带文件：`.meta.json`（尺寸、帧率、裁切 `crop`）、`.shots.json` + `.sheetNN.jpg`（shots.py log）、`.lines.json/.txt` + `.vocals.wav`（lines.py）、`.source.json`（fetch.py get 写，conform 靠它找原链接）、`.hi.json`（conform 写的高清片段清单）。
- **EDL 里的路径都相对 EDL 文件本身**。EDL 在 `source\`，素材就写 `../assets/proxy/xxx.mp4`。绝对路径也行。

在作品目录下跑：
```
py $S\edl_check.py source\edl.json                    # 先查：每镜是否待在素材的一个镜头里、台词落在哪
py $S\render.py source\edl.json --still 8.4 --still 22.5   # 只出这几个时刻的 PNG，不混音，最快
py $S\render.py source\edl.json --preview             # 半尺寸、快编码 → renders\edl_preview.mp4
py $S\render.py source\edl.json --from 20 --to 32 -o renders\part.mp4   # 只渲一段
py $S\render.py source\edl.json                       # 全尺寸 crf 18 → renders\edl.mp4；约每秒 10 帧（54 秒的片约 2 分钟）
py $S\qc.py renders\edl.mp4                           # 渲完先跑 qc，看 cuts.jpg
```

- `-o` 不写：EDL 所在文件夹叫 `source`，就输出到作品目录的 `renders\<EDL 文件名>.mp4`（`--preview` 加 `_preview`）；否则输出到 EDL 旁边的 `renders\`。`--from/--to` 不加 `-o` 会覆盖同名的整片。
- 渲染还会写：`<输出>.mp4.timeline.json`（fps、长度、切点 cuts、拍点 beats、音乐接缝 seams、每镜的 i/t0/t1/src/fx/in/beat/note；qc 读它，`--still` 时也写）；`<输出>.wav`（混音临时文件，渲完删，中途打断会留下）；`--still` 的图叫 `renders\<EDL 名>_still_<秒>.png`（秒数取整到帧）。
- qc 打印响度、黑场、冻帧、不在 1/4 拍上的切点、重复用的镜头，出 `.cuts.jpg`（每镜一格，切点后第 2 帧）和 `.strip.jpg`（每 0.25 秒一格）。black 镜头、≥0.6 秒的 freeze、按秒剪的切点可能被列出来，属于预期。

edl_check 的输出（每镜：时间线起止、第几拍、素材、源秒、落在素材的哪个镜头里、note；有问题的行尾带 `<--`；后面列出每句台词的时间线位置）：
```
#17  16.41- 16.88 b 35.0  3_nprPycZow     1.00-   1.47  shot 0.00-5.21         空间站
#53  39.38- 41.26 b 84.0  Rvns5DaW-ug   344.90- 346.78  shot 340.34-352.48     停：大特写，他喊 STAY…
length 54.09s, 59 clips, 0 problem(s); music seams at 22.50, 30.00, 44.55
```

## 2. 时间怎么写

- 单位都是秒。**源秒** = 素材文件里的第几秒（`in`、`say`、dialog 的 `in/out`）；**时间线秒** = 成片里的第几秒，0 = 第一帧。
- 镜头、texts、subs 的 `at`、`until`，dialog、sfx 的 `at`，都能写秒数，也能写 `"bN"` = 时间线上第 N 拍。b0 = 音乐起点处（往前 30 毫秒以内也算）或之后的第一拍。N 可以带小数（`"b20.5"`），可以是负数。用了 `music.segments` 就按拼好的时间线数拍，接缝之后接着数，静音段里照常数。
- 拍子从 `<音乐文件>.beats.json` 读。没有这个文件：打印 note，按 120 bpm（每拍 0.5 秒）算；整条片没有 music：也按每拍 0.5 秒，不提示。
- 镜头一个接一个排，每镜从上一镜结束处开始。长度三选一，优先级 beats > until > dur，都不写 = 1 秒：`beats` 拍数，结尾吸到拍子网格上（0.5 = 半拍，0.25 = 1/4 拍）；`until` 结束时刻（`"b32"` 或秒）；`dur` 秒数，不吸附（音乐停了以后、一句一镜的段落用它）。
- **beats 怎么吸附**：按拍数的精度（整数 → 整拍，x.5 → 半拍，x.25 → 1/4 拍，其他 → 1/8 拍），先把起点往前吸到网格，再往后数拍数。所以起点不在拍上时（前面有 dur 镜头），这一镜会比写的短：起点在第 3.3 拍、beats 2 → 结束在第 5 拍（实际 1.7 拍）；beats 0.5 → 结束在 3.5 拍（实际 0.2 拍）。切点最后取整到帧：帧号 = round(秒 × fps)。

## 3. 顶层字段

| 字段 | 意思 | 默认 |
|---|---|---|
| `size` | 成片 [宽, 高]，偶数。`--preview` 时减半 | [1440, 1080] |
| `fps` | 帧率。素材是 23.976 的写 24（第 9 节） | 30 |
| `music` | 配乐，见下表 | 无 |
| `clips` | 镜头，按顺序排，第 4 节 | 必填 |
| `dialog` | 声音来自别处的台词，第 5 节 | [] |
| `texts` | 屏幕上的字，第 6 节 | [] |
| `subs` | 不跟声音走的独立字幕，第 6 节 | [] |
| `sfx` | 合成音效 `{"kind": "whoosh"/"hit"/"boom"/"riser", "at": 时间, "gain_db": -10, "dur": 秒}`。at 必填；dur 默认 whoosh 0.45、hit 0.6、boom 1.6、riser 1.5；不会被压低 | [] |
| `look` | 全片调色，见下表。不写也套默认值 | 见下 |
| `end_fade` | 片尾最后几秒画面淡到黑，字一起淡；不管声音 | 0 |
| `sub_style` | 台词字幕（dialog / say 带的 zh、en）的样式，第 6 节 | "a" |
| `sub_scale` | 台词字幕放大倍数，只对 serif、thin 有用 | 1.0 |
| `length` | 强制片长（秒），一般不写；写短了截掉结尾 | 最后一镜的结束 |

`title`、`idea` 之类代码不读，可以当备注。`music` 里：

| 字段 | 意思 | 默认 |
|---|---|---|
| `file` | 配乐文件（相对 EDL），要先 `music.py analyze` | 必填 |
| `start` | 原曲第几秒对上时间线 0 秒；有 segments 时不用 | 0 |
| `segments` | 把原曲几段按顺序拼起来，见下 | 无 = 从 start 一直放 |
| `xfade` | 每个接缝默认的交叉淡化秒数（等功率，以接缝为中心各占一半） | 0.05 |
| `gain_db` | 音乐音量 | 0 |
| `fade_in` / `fade_out` | 片头淡入 / **片尾**淡出秒数（按整条片算，不是每段） | 0 / 1.5 |

segments 每一项（单位原曲秒）：`[a, b]` 原曲 a 到 b；`{"from": a, "to": b, "xfade": 1.5}` 同上，这个 xfade 管**这一段前面那个接缝**；`{"rest_beats": 2}` 静音 2 拍，拍子照常往下数；b 或 to 写 `null` = 放到原曲结尾。接缝要落在小节线上（`music.py seams` 打分挑）；离最近的拍超过 30 毫秒，渲染时打印 `note: music seam ... off the beat grid`。星际穿越：`[[33.198, 55.697], [70.686, 78.184], [108.201, 122.751], {"from": 3.0, "to": null, "xfade": 1.5}]`。

`look`（字画在调色之后，不受影响。星际穿越：`{"contrast": 1.06, "saturation": 0.98, "grain": 0.02, "vignette": 0.25, "normalize": 0.25, "sharpen": 0.2}`）：

| 键 | 默认 | 作用 | 键 | 默认 | 作用 |
|---|---|---|---|---|---|
| `contrast` | 1.06 | 对比，绕中灰拉开 | `saturation` | 1.04 | 饱和度，1 = 不变 |
| `gamma` | 1.0 | 大于 1 中间调变亮 | `lift` | 0 | 抬黑位 |
| `fade` | 0 | 褪色感（黑位抬起、白位压低） | `warmth` | 0 | 正 = 偏暖，负 = 偏冷 |
| `tint` | 0 | 正 = 减绿（偏品红），负 = 偏绿。和镜头上的 tint 不是一回事 | `split` | 0 | 暗部偏青、亮部偏暖 |
| `grain` | 0.03 | 颗粒 | `vignette` | 0.2 | 暗角 |
| `sharpen` | 0.25 | 锐化 | `glow` | 0 | 亮部光晕 |
| `ca` | 0 | 色差（红通道放大、蓝通道缩小的比例） | `normalize` | 0.5 | 每镜自动曝光：按这一镜第一帧的平均亮度，用 gamma 往中灰拉；0 = 关，1 = 拉满，高光不会爆 |

## 4. 镜头 clips

每一帧的处理顺序：取景（focus、zoom、mirror）→ tint → shade → layout → 自动曝光 → 切点效果 → 重叠转场 → look → 字 → end_fade。

| 字段 | 意思（单位） | 默认 / 什么时候用 |
|---|---|---|
| `src` | 素材路径（相对 EDL） | 必填（black、color 镜头不用） |
| `in` | 入点，源秒 | 必填（同上） |
| `beats` / `dur` / `until` | 长度，第 2 节 | 都不写 = 1 秒 |
| `at` | 把起点挪到某个时刻 | 不写 = 接着上一镜。写了会和前一镜重叠或留空，代码不查，别用 |
| `note` | 为什么用这一镜 | 每镜都写；edl_check、timeline.json、qc 的 cuts 图都显示它 |
| `focus` | 画面中心对准哪一点：`[x, y]` 是裁掉黑边后的素材画面里的比例（0–1）；`"center"` = [0.5, 0.5]；`"auto"` = 找人脸 | `"auto"`：取开头和中间两帧找脸，y 往下挪 0.06（限在 0.3–0.62），找不到就居中。靠边会被挡住，画面不会露出素材外面 |
| `focus_end` | `[x, y]`，镜头里从 focus 慢慢摇到这里 | 不摇 |
| `zoom` | 放大倍数。1 = 素材"铺满"画面（多出的边裁掉）；一个数 = 不变，`[起, 止]` = 推或拉 | 长度 ≥1.4 秒且不是 freeze：`[1, 1.035]` 慢推；更短：1。完全不动写 1 |
| `ease` | zoom 和摇镜缓入缓出；`false` = 匀速 | true |
| `speed` | 播放速度，0.75 = 慢放；用掉的素材 = 长度 × speed | 1。台词声音不跟着变速 |
| `interp` | speed < 1 时补帧：`"blend"` 相邻帧叠化，`"mci"` 运动补偿插帧（最慢） | 不写 = 重复帧 |
| `reverse` | 倒放 | false。按代码，读素材时多读的 0.6 秒也被倒过来放在最前面，实际倒放的大约是 in+0.6 到 in+0.6+长度×speed；用前 `--still` 看 |
| `freeze` | 定格在这一镜第一帧 | false。默认不推，可以自己写 zoom |
| `black` / `color` | 黑场 / 纯色 `"#rrggbb"`，不用 src、in | 这一镜的 say、audio 被忽略 |
| `mirror` | 左右翻转 | false |
| `tint` | `[r, g, b]` 三个乘数，乘完截到 255。`[1.25, 1.25, 1.25]` = 提亮，`[0.9, 0.98, 1.1]` = 偏冷 | 无。单镜提亮只有它（第 9 节） |
| `shade` | `[y0, a]`：从画面高度 y0 处（0 = 顶）往下越来越暗，到底边亮度剩 1−a；给下半部的字垫底 | 无。例 `[0.55, 0.7]` |
| `fx` / `fx_len` | 切进这一镜的那一刀用什么效果、几帧，第 7 节 | `"cut"`。第一镜的 fx 不起作用 |
| `audio` | 这一镜自己的原声，值是 dB（如 -6）；读素材原混音（不分离人声），随 speed 变调 | 不写 = 不出声 |
| `duck_db` | 有 audio 时：这一镜期间音乐压低多少 dB。有 say 时：跟着台词走（第 5 节） | 无 |
| `say` | `[a, b]`：这一镜素材里 a–b 源秒的台词，第 5 节 | 无 |
| `layout` | 分屏等，见下 | 无 |

### 分屏 layout

例：`"layout": {"type": "split", "b": {"src": "...", "in": 311.18, "focus": [0.52, 0.41]}, "b_k": 6, "b_fade": 6, "anim": false, "gap": 4}`。左半边是本镜，右半边是 `b`。b 是一个小镜头：src、in 必填，focus、zoom、tint、speed 等自己写（不继承本镜的；zoom 默认 1，不推），长度跟本镜一样，声音不进混音。

| 字段 | 意思 | 默认 |
|---|---|---|
| `b_k` | 右半边从本镜第几帧才出现，之前是黑的 | 0 |
| `b_fade` | 出现后用几帧从黑里淡起来；0 = 直接跳出 | 0 |
| `gap` | 中缝宽（像素） | 4 |
| `anim` | 头 6 帧两半上下错开、滑回原位（是卷动，画面会从另一头卷出来） | true |
| `swap` | 本镜放右边、b 放左边。b_k、b_fade 管的始终是右半边 | false |
| `ax` / `bx` | 本镜 / b 在半边里取哪一段：先按各自的 focus、zoom 取好整幅，再横着截半幅，0.5 = 截中间 | 0.5 |

其他 type（这个风格没用过）：`pip`（本镜模糊压暗当底，`bg_blur` 默认 14、`bg_dim` 默认 0.7；`items` 每项是一个小镜头加 `rect: [x, y, w, h]`（0–1）和 `k`（第几帧弹出））、`panels`（本镜模糊当底，`n` 条圆角竖条错开滑入，默认 3，`srcs` 给每条的镜头）、`nested`（本镜画面一层套一层缩小，`levels` 默认 3）。

## 5. 台词

- **镜头上的 `say: [a, b]`**：声音和画面是同一个文件。引擎自动对齐：声音从画面放到源 a 秒那一刻开始（at = 这一镜 t0 + (a − in) / speed），可以一直说到后面几镜（画面照切，声音不断）。这一镜上的 `zh` `en` `gain_db` `duck_db` `stem` `fade_out` `sub_lead` `sub_hold` `sub_style` `sub_scale` `sub_x` `sub_y` 会带到这句台词上（`norm`、`fade_in` 带不过去）。
- **顶层 `dialog`**：声音来自别处（另一个素材，或同一素材的另一遍），at 自己算。画面嘴型不是这句、借别处的喊声、慢放镜头配台词，都用它。

| 字段 | 意思 | 默认 |
|---|---|---|
| `src` / `in` / `out` | 声音来源和源秒 | 必填 |
| `at` | 从时间线哪里开始（秒或 "bN"） | 0 |
| `stem` | `"vocals"` = 用 `<src>.vocals.wav`（lines.py --sep 分出的人声，没有原片配乐）；`"mix"` = 原混音 | "vocals"；文件不在就用原混音（没显式写 stem 时不提示） |
| `norm` | 先拉齐：这句响的那一半拉到 -16 dBFS（最多 −12 / +30 dB），不同电影的台词一样响 | true |
| `gain_db` | 拉齐之后再加减 | 0 |
| `duck_db` | 说这句时音乐压低多少 dB（提前 0.12 秒压下去，说完 0.3 秒回来）；0 或正数 = 不压 | -10 |
| `fade_in` / `fade_out` | 声音头尾淡入淡出（秒）；喊声尾巴截得急就加大 fade_out | 0.015 / 0.04 |
| `zh` / `en` | 这句的字幕，说的时候显示 | 无 = 不出字幕 |
| `sub_lead` | 字幕相对声音提前多少秒（负 = 早出，正 = 晚出） | -0.05 |
| `sub_hold` | 说完字幕多留几秒 | 0.3 |
| `sub_style` / `sub_scale` | 覆盖顶层设置 | 顶层值 |
| `sub_x` / `sub_y` | 字幕位置（0–1）；sub_x 只对 serif、thin、c 有用 | 0.5 / 样式默认 |

- 声音永远按原速放。镜头 speed≠1 时 say 只有开头对得上嘴型。最后整条混音拉到 -14 LUFS、真峰值 -1.5 dB（qc 复查）。
- 混音时每句打印一行：人声响度、底下的音乐（压低之后，含音效）、差值。差值 < +8 dB 标 `<- music too loud for this line`（把 duck_db 写得更负或加 gain_db），> +20 dB 标 `<- music very low`（音乐停了的地方属正常）：
```
  line  22.75- 26.00s  voice  -16.6  music under it  -25.4  ->  +8.8 dB  xGrbLfi1VJ8
  line  44.16- 45.66s  voice  -16.7  music under it  -43.3  -> +26.6 dB  Rvns5DaW-ug  <- music very low
```

## 6. 字

### texts

| 字段 | 意思 | 默认 |
|---|---|---|
| `text` | 文字。`[ ]` 是高亮标记：括号不显示，里面的字变红 | 必填 |
| `at` + `dur` / `beats` / `until` | 什么时候出、显示多久（同第 2 节） | at 0，1.5 秒 |
| `style` | 样式，见下 | "big" |
| `x` / `y` | 位置（0–1）；y 是这一行的竖直中心 | 0.5 / 0.5 |
| `anchor` | `"c"` 中心对 x，`"l"` 左边对 x，`"r"` 右边对 x | "c" |
| `size` | 字号 = 1080 高画面上的像素，按画面高度缩放（竖屏 1920 高会放大 1.78 倍） | 看样式 |
| `tracking` | 字距，字号的倍数：0.2 = 每两个字之间多空 0.2 个字号 | 看样式 |
| `fade` | `[淡入, 淡出]` 秒。只对普通字有用（glass、type、words 固定 4 帧） | [0.13, 0.13] |
| `anim` | `"fade"`；`"pop"` 从 1.25 倍缩回；`"rise"` 从下面 24 像素升上来；`"type"` 打字机，前 45% 时间里逐字出；`"none"` 不淡入（仍淡出） | "fade" |
| `words` | 一行字逐词出：`["I'm", "coming", ["back.", 0.6]]`，每个词在 at 之后第几秒弹出（3 帧淡入、从 1.18 倍缩回），没写秒的在前一半时间里平均分；起点由 text 的位置定，词之间一个空格。排版没算字距，tracking > 0 会挤 | 无 |
| `kind` `weight` `fill` `shadow` | 字体、粗细、颜色 `[r, g, b, a]`（0–255）、阴影浓度（0–1）。只有 moment 全读；big 读 kind、fill；thin 读 fill | 看样式 |

| style | 样子（字号是 1080 高时的像素） | 读哪些 |
|---|---|---|
| **`moment`** | 这个风格的字：叙事重点那几处，安静、宽字距，不描边不发光，一层柔阴影（模糊 5、往下 1 像素）。默认 zh_serif、weight 400、54 号、字距 0.2、米白 [246, 244, 238, 245]、阴影 0.5 | kind、weight、size、tracking、fill、shadow |
| `big`（默认；写错的名字也落到这里） | zh_bold 86 号，字距 0.12，白，阴影 0.55 | kind、size、tracking、fill |
| `serif` | zh_serif weight 600，64 号，字距 0.25 | size、tracking |
| `thin` | en_thin 96 号，暗金 [205, 188, 130, 225] | size、fill、tracking |
| `caption` | 全大写 en_thin 30 号，默认 y 0.94 | size、y、tracking |
| `quote` / `glass` / `lyric` | zh_light 38 号 / zh_bold 38 号字距 1.05 带毛玻璃底 / en_display 110 号黄字红投影 | size（glass 还读 tracking） |

星际穿越的 moment：开场立论句中文 zh_serif weight 500、72 号、字距 0.12，英文 pf_italic 32 号在下面；STAY 用 pf_roman 150 号、字距 0.32；fill 都是 `[246, 238, 222, 250]`，fade `[0.07, 0.2]`（快出慢收）；片名 fade `[0.45, 0.12]`、shadow 0.9。

`kind` 可选：
- 中文：`zh_serif` 思源宋体（默认 400）、`zh_bold` 思源黑体（默认 700）、`zh_sans` 思源黑体（默认 400），这三个是可变字体，`weight` 只对它们有用；`zh_reg` 微软雅黑，`zh_light` 微软雅黑 Light。
- 英文：`pf_roman` / `pf_italic` / `pf_italic_med` = Playfair Display 正体 / 斜体 / 中粗斜体；`en_serif` = Times New Roman；`en_serif_bold` / `en_serif_italic` / `en_display` = Georgia 粗 / 斜 / 粗斜；`en_thin` / `en_thin_italic` / `en_sans_bold` = Segoe UI Light / Italic / Bold。
- 也可以直接写字体文件名，如 `"MiSans-Medium.ttf"`（先找 `library\fonts\`，再找 Windows 字体）。

### 台词字幕样式 `sub_style`，和 `subs`

| 样式 | 样子 |
|---|---|
| **`serif`** | 这个风格用的：小、克制，不描边不发光，一层柔阴影。中文 zh_serif weight 500、35 号、字距 0.04；英文 Georgia 斜体 26 号在下面；默认 y 0.872；`sub_scale` 整体放大（星际穿越 1.3） |
| `thin` | 同样克制：中文思源黑体 300、34 号；英文 Segoe UI Light 24 号 |
| `a` / `d` | 中文粗黑 40 号 + 英文 Times 37 号黄色 / 中文粗黑 46 号 + 英文 Segoe 斜体 36 号 |
| `c` | 只有中文，左对齐 48 号带白色辉光，`[ ]` 里的字红色 |
| 其他名字 | 只出一行（zh，没有就 en），粗黑 44 号 |

`subs` 是不跟声音走的字幕：`{"at", "dur"/"beats"/"until"（默认 1.5 秒）, "zh", "en", "style", "x", "y", "scale", "anim"}`。它不读顶层 sub_style、sub_scale：style 默认 "a"，放大用 `scale`。

## 7. 切点效果 fx

`fx` 写在**切进去的那一镜**上：既改上一镜最后几帧（出画），也改这一镜头几帧（进画）。`fx_len` 改帧数，不写用下表的默认。这个风格只在落拍、段落交接、高潮连甩这几处用下面这些"门"，其余硬切：

| fx | 默认帧数 | 出画（上一镜最后几帧） | 进画（这一镜头几帧） |
|---|---|---|---|
| `whip_in` | 3 | 不动（参考 A 的甩入只糊进来的镜头） | 前 3 帧横向拖影、往右错开一点，由糊到清，第 3 帧只剩一点 |
| `whip_in_l` | 3 | 不动 | 同上，往左 |
| `whip_in_u` / `whip_in_d` | 3 | 不动 | 竖向，往上 / 往下 |
| `flash_blur` | 4 | 最后 4 帧越来越糊、越来越过曝 | 前 4 帧从糊和过曝里退回来 |
| `flash` | 3 | 最后 1 帧蒙 25% 白 | 白 85% → 45% → 15% |
| `rays` | 12 | 无 | 前 12 帧，亮部从画面中上部（宽 1/2、高 0.42 处）向外放射光，慢慢收掉 |
| `leak` | 8 | 无 | 第 1 帧 70% 白，之后一团暖橙光从左扫到右、渐弱 |
| `blur_dark` | 14 | 最后 3 帧糊掉、压暗 30%（固定 3 帧，不看 fx_len） | 前 14 帧从几乎全黑缓缓亮起来；片名横跨两镜时用 |

代码里有、这个风格基本不用的：切点类 `whip` / `whip_r` / `whip_l` / `whip_u` / `whip_d`（出画 2 帧 + 进画 2 帧的甩）、`zoom`（进画 7 帧从 1.22 倍收回）、`zoom_out`（出画 4 帧放大）、`glitch`、`rgb`、`shake`、`strobe`、`blur`、`flash_black` / `dip`（压黑闪）；重叠转场 `dissolve`、`white_dissolve`、`luma`、`circle`、`wipe` / `wipe_l` / `wipe_r`、`strips`、`slide` / `slide_l` / `slide_r`（两镜叠着过渡，切点在中间，上一镜多放半段、这一镜提前半段，两头素材都要有富余）。

## 8. build 脚本怎么写

剪辑表不手写，用 `source\build.py` 生成：镜头按段落写成 Python 列表，一镜一行、带 note；脚本先让引擎排一遍时间线拿到每镜的 t0/t1，再算台词和字放哪，最后写出 edl.json。改了就重跑 build，再 edl_check。骨架（从星际穿越的 build_v4.py 精简）：

```python
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent                       # works\<作品>\source，EDL 也写在这里
sys.path.insert(0, str(Path.home() / ".claude/skills/mixcut/scripts"))   # skill 的 scripts 目录
import render
SRC = dict(A="../assets/proxy/aaaaaaaaaaa.mp4", B="../assets/src/bbbbbbbbbbb.mp4")   # 简称 -> 路径（相对 EDL）
PRIVATE = ("say_src", "say_at", "big")                       # 只给脚本用，写出前去掉
def clip(src, t_in, beats, note="", **kw):                   # beats 写 None 就自己给 dur
    return dict(src=SRC[src], **{"in": t_in}, note=note, **({} if beats is None else {"beats": beats}), **kw)

CLIPS = [   # big 要配 say 或 say_src：(这个短句在源里第几秒说出来, 字, y)
    clip("A", 44.00, 7, "开场一句话", say=[44.30, 47.12], duck_db=-3,
         big=[(44.30, "前半句，", 0.17), (45.44, "后半句。", 0.26)]),
    clip("B", 69.11, 1, "落拍第一刀", fx="flash_blur"),
    clip("A", 120.0, 4, "停：画面用这里，喊声借 B 那一遍", say=[3.40, 4.55], say_src="B", say_at=0.30, duck_db=-13),
]

def build():
    edl = {"size": [1440, 1080], "fps": 24, "sub_style": "serif", "sub_scale": 1.3, "end_fade": 0.9,
           "music": {"file": "../assets/music/song.m4a", "segments": [[33.198, 55.697]], "fade_out": 1.2},
           "look": {"normalize": 0.25, "grain": 0.02}, "dialog": [], "texts": [],
           "clips": [{k: v for k, v in c.items() if k not in PRIVATE} for c in CLIPS]}
    tl = render.layout_clips(edl, render.music_grid(edl, HERE)[0])   # 每镜排好后的 t0 / t1（时间线秒）
    for c, e, t in zip(CLIPS, edl["clips"], tl):
        line_at = line_a = None
        if c.get("say_src"):                                 # 声音借别处：从镜头上拿掉，挪成一条 dialog
            a, b = e.pop("say")
            d = dict(src=SRC[c["say_src"]], **{"in": a, "out": b}, at=round(t["t0"] + c.get("say_at", 0.02), 3),
                     duck_db=e.pop("duck_db", -6), gain_db=e.pop("gain_db", 0))
            edl["dialog"].append(d)
            line_at, line_a = d["at"], a
        elif c.get("say"):
            line_at, line_a = t["t0"] + (c["say"][0] - c["in"]) / c.get("speed", 1), c["say"][0]
        for src_t, text, y in c.get("big", []):              # 每个短句在说出来那一下出，留到这一镜结束
            on = line_at + (src_t - line_a)
            edl["texts"].append(dict(at=round(on, 3), dur=round(t["t1"] - on, 3), text=text, y=y, style="moment",
                                     kind="zh_serif", weight=500, size=72, tracking=0.12, fade=[0.07, 0.2]))
    (HERE / "edl.json").write_text(json.dumps(edl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(tl)} clips, {tl[-1]['t1']:.2f}s")
build()
```

要点：
- 只给脚本用的键放 `PRIVATE`，写出前去掉。build_v4 用了这些：
  - `say_src` + `say_at`：声音不是这一镜画面里的（借另一遍、另一个素材）。从镜头上拿掉 say，连同 duck_db、gain_db 挪成一条 dialog，at = 这一镜 t0 + say_at（默认 0.02），duck_db 默认 -6；有 fade_out 也带过去。
  - `line=dict(src, a, b, at, duck_db)`：这一镜开始后 at 秒放一句别处的台词（duck_db 默认 -6），盖过后面几镜（"23 年"那句底下切了 4 刀）。
  - `big="名字"` / `words="名字"`：这一镜配的大字 / 逐词字，名字对应脚本里写好的一组 `word(...)` 调用（big_for / words_for）。骨架里直接把 (源秒, 字, y) 写在镜头上，意思一样。它和 texts 里引擎认的 `words` 字段不是一回事。
  - 普通的 `say` 不是私有键，留在镜头上交给引擎。
- **字按说出来的时间算**：先记下这句台词在时间线上从哪开始（line_at）、对应源里第几秒（line_a）。say：line_at = t0 + (say[0] − in) / speed；say_src：line_at = 那条 dialog 的 at；line：line_at = t0 + line 的 at。某个词在源里 src_t 秒说出来，它在时间线上就是 `on(src_t) = line_at + (src_t − line_a)`（声音不变速，所以这里不除 speed）。词的源秒查 `<src>.lines.json`（lines.py 的逐词时间），最好再对一下人声能量。
- 字一般留到这一镜结束（dur = t1 − on）；一句跨几镜就用后面那镜的 t1。片名按段落切点倒推（星际穿越：落拍那一刀前 1.75 秒出）。跑完打印每段起点和总长，和段落表对一下（build_v4 还用 `grid.resolve("b88")` 核对段落拍号）。

## 9. 坑（都在代码里看得到）

- **tint 是乘法**：乘完截到 255，亮部会被削平。它也是**单个镜头唯一的提亮办法**，look 里的 gamma、lift、normalize 管全片。暗镜头写 `"tint": [1.25, 1.25, 1.25]`（星际穿越用过 1.2–1.6）。
- **镜头上别写 `gain`**：代码只看有没有这个键，有就关掉这一镜的自动曝光（normalize），值不起作用。音量用 `audio`（镜头原声）或 `gain_db`（台词）。
- **裁切在 `<素材>.meta.json` 的 `crop`**（[x, y, w, h]，素材像素）。shots.py log 用 cropdetect 只测黑边，漏掉的细黑线、角上的台标水印要手改。改完 focus 会跟着变（focus 是相对裁切后画面的），再看一眼。`shots.py log --force` 会重测并覆盖手改的 crop。
- **高清片段的 crop 要跟着改**：conform 下载时按"高清宽 / 代理宽"（如 1920 / 640 = 3）把代理的 crop 换算一次，写进 `<高清文件>.meta.json`，之后不再同步。改了代理的 crop，`source\assets\hi\` 里对应文件的 meta.json 也按同样比例手改。
- **高清片段是悄悄换上的**：conform 写 `<素材>.hi.json`；渲染时某一段完整盖住这一镜要读的范围（含重叠转场多读的帧）、文件还在、匹配误差 err < 50，才用它，否则用 360p 代理，不提示。改了入点、长度、speed 要重跑 conform。conform 只管有 `.source.json`、且高度低于目标 90% 的素材。
- **素材 23.976 fps，成片写 `"fps": 24`**：读素材时用 ffmpeg 的 fps 滤镜换帧率，放进 30 fps 每 5 帧重复 1 帧，快切发顿（星际穿越 v4 改 24 后没了）。
- **越过素材里的镜头边界会闪一下下一镜**。edl_check 报 `runs X s into the next shot` 或 `starts at the very end of a shot`（离镜头结尾不到 0.2 秒）。它只查主镜头的 [in, in + 长度 × speed]，不查分屏 b、pip items、重叠转场多读的帧、reverse，这些自己留余量。
- **stem 默认 vocals**：素材没跑过 `lines.py --sep` 就没有 `.vocals.wav`，台词会带着原片配乐，而且不提示（只有显式写了 stem 才打印 note）。
- **say 声音不变速**（第 5 节）；**beats 起点不在拍上会变短**（第 2 节）；**顶层 sub_style / sub_scale 不管 subs**（第 6 节）。
- **名字写错都不报错**：fx 写错 = 硬切；texts 的 style 写错 = big（86 号粗黑）；kind 写错 = 一个很小的默认字体；layout 的 type 写错 = 没有分屏。拿不准就 `--still` 看一眼。
- **end_fade 只淡画面**，声音靠 `music.fade_out`（默认 1.5 秒，在片尾），台词不会被淡。
- **focus "auto" 有缓存**：按"素材 + 入点"存在 `source\.focus_cache.json`，改了长度不会重测，要重测就删掉那一条。人脸模型是 `library\models\face_detection_yunet_2023mar.onnx`，没有就全部居中。
- **不写 look 也会调色**（对比 1.06、饱和 1.04、颗粒 0.03、暗角 0.2、锐化 0.25、normalize 0.5）。要原样输出得把这几个显式写成 1 或 0。
