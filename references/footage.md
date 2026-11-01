# 找片、下片、台词、镜头

第 2 站（素材）用。网很慢（约 200 KB/s，GitHub 只有几十 KB/s），所以一切按"先小后大"：长片段先下 360p 代理来看、来挑、来剪，定稿后只把用到的那几秒拉成 1080p。

命令里的 `python` 是装好依赖的 Python，脚本在 skill 的 `scripts\`，都在作品目录（`works\<日期>_<主题>\`）里跑。

## 1 去哪找

YouTube 上最好用的几类：

| 类型 | 好处 | 坑 |
|---|---|---|
| 官方预告（同一部片常有 2–4 版） | 1080p，镜头精挑过、亮 | 台词被配乐压着；不同版本镜头重复很多 |
| 片段频道的整场戏（如 MOVIECLIPS） | 台词完整，一场戏前后都在，能找到"先给脸再出声"的入点 | 有黑边、角标水印；多是 360p 才下得快 |
| 特辑、幕后、采访 | 能找到立论句（主演说的一句话） | 画面多是采访，不能当镜头用 |
| 结局、名场面合集 | 一下拿到好几场 | 剪过一遍，切点不干净 |

```
python fetch.py search "Interstellar official trailer" --n 8      # id、时长、播放量、频道、标题
python fetch.py search "Interstellar bookshelf scene"
```

主题模式（还没跑过）：先列 6–12 部候选片，每部搜"<片名> scene <关键词>""<片名> <那句台词>"，只留能贡献一句台词或一个状态的片子（`story.md` 第 4 节）。

## 2 下载

```
python fetch.py get <id> [<id> ...] --dir assets/proxy --h 360          # 长片段：360p 代理
python fetch.py get <id> --dir assets/src --h 1080                      # 短的官方预告：直接 1080p
python fetch.py get <id> --dir assets/proxy --h 360 --section 30-95     # 只要其中一段
python fetch.py music "Hans Zimmer Cornfield Chase" --dir assets/music  # 配乐（搜索第一条，只要音频）
```

每个文件旁边会写 `<文件>.source.json`（标题、频道、网址、时长），conform 要用它找回原片。

**定稿以后拉高清（第 6 站）：**

```
python fetch.py conform source/edl.json [--h 1080] [--pad 1.5]
```

它只拉剪辑表里用到的那几秒（每段前后多要一点，因为切在关键帧上会晚几秒），放进 `source\assets\hi\`（剪辑表旁边），写 `<代理>.hi.json`，并按缩略图把高清片段和代理逐帧对齐。渲染时自动用高清；对不齐（误差 ≥50）的片段会被跳过，照样用代理。

查哪些镜头还在用代理：渲染前跑 `edl_check.py`，渲染后看 `qc.py`；交付说明里逐镜列出来（镜号、时间、是什么）。

## 3 YouTube 要登录的坑

现象：下载报 "Sign in to confirm you're not a bot" 或者要 cookies，什么都下不下来。《星际穿越》v4 的高清回拉就卡在这里，5 个镜头只能用 360p 放大，偏软。

规矩：

1. **停下来问用户**：说清楚是哪几个镜头、不给的后果（偏软）、给了要做什么（让 yt-dlp 读他浏览器里的 YouTube 登录）。
2. 他同意了，才在命令后面加 `--cookies-from-browser chrome`（或 `edge`、`firefox`，用他平时登录 YouTube 的那个）；`get`、`music`、`conform` 都能带。
3. 不要自己去读浏览器的 cookies 文件，不要把 cookies 导出存到任何文件夹，不要写进脚本或剪辑表。
4. 他不同意，就用代理版交付，把偏软的镜头列在说明里。

## 4 台词

```
python lines.py assets/proxy/*.mp4 assets/src/*.mp4 --sep     # 分离人声 + 逐词时间
python lines.py find assets "coming back"                     # 在所有素材里搜一句
```

- `--sep` 先用 demucs 把人声从配乐里分出来（CPU，慢，一条几分钟的片段要几分钟），存成 `<素材>.vocals.wav`；whisper 在分离后的人声上转写，出 `<素材>.lines.txt`（每行"起-止 台词"）和逐词时间。预告片里配乐压着台词，一定要分离。
- **转写会错**：A 里"Please! I'm a star!"被听成了 "Because you see"，D 里 "I'll be back" 被听成 "I'm sorry"。要用的句子单独切出来再转一遍，或者分离人声和原混音各转一遍对着看；词的边界看人声能量曲线核。
- 数字、倒计时这种，转写的词边界和能量峰对不上 0.3–0.8 秒，核不准就不用。
- 剪辑表里的台词默认用人声轨（`stem: "vocals"`）；分离得不干净时换原混音（`"mix"`）。
- 结尾要用、怕听不清的句子：单独放大转写，看每个词的置信度（《星际穿越》结尾那句每个词 0.92 以上，人声比底噪高约 25 dB，才敢用）。

## 5 镜头

```
python shots.py log assets/proxy/*.mp4 assets/src/*.mp4          # 切镜头、量、出联系表
python shots.py peek assets/proxy/a.mp4@63.2+2 assets/src/b.mp4@10 --out peek.jpg
python shots.py strip assets/proxy/a.mp4 63.0 64.5 --n 12 --out strip.jpg
python shots.py pool assets/proxy assets/src --top 72 --out pool.jpg
```

- `log` 写三样：`<素材>.meta.json`（尺寸、帧率、**裁切 crop**）、`<素材>.shots.json`（每个镜头的起止、亮度、饱和、冷暖、清晰度、运动、脸的位置和大小、画面里有没有字）、`<素材>.sheetNN.jpg`（联系表：每个镜头中间一帧，画了 4:3 裁切框和脸）。
- `peek`：一张图验很多个候选入点，`@63.2+2` = 63.2、64.2、65.2 秒三帧。挑镜头时最常用。
- `strip`：一段时间里均匀取 n 帧，挑精确入点（等回头、等张嘴）。
- `pool`：跨所有素材挑出综合分最高的镜头，开始挑之前先看一眼有什么。
- **黑边和水印**：片段频道的素材常有。在 `<素材>.meta.json` 的 `crop`（`[x, y, 宽, 高]`，代理像素）里收紧，渲染和 conform 都按它裁（高清版按比例换算）。v4 的一批 360p 素材要收到 `[0, 47, 640, 266]` 才没有黑边和角标。
- 联系表和 peek 图都很大，**看之前缩到 1000 宽以内**（`small.py`）；看很多张的活交给子代理。

## 6 素材登记

在作品目录写一张素材表（放 `brief.md` 或 `source\assets.md`），给每个素材起一个短名，build 脚本里用短名：

| 简称 | 文件 | 内容 | 清晰度 |
|---|---|---|---|
| BYE | `assets\proxy\yMpdgnUYVNo.mp4` | 告别那场，含倒计时 | 360p |
| T2 | `assets\src\Lm8p5rlrSkY_m3u8.mp4` | 预告片，动作镜头最多，**没有声音** | 1080p |

（例子是《星际穿越》的。）同一部片的几版预告大量重复，登记时写一句"和谁重复"，省得挑镜头时一镜用两遍（`qc.py` 也会报重复镜头）。

## 7 存放和版权

- 全部放在作品目录：`works\<日期>_<主题>\assets\{src, proxy, music}`，高清回拉在 `source\assets\hi\`。
- 电影片段、配乐、参考片不进 skill，不进公开仓库，不进 `library\`。
- 发布以后，下载的素材和 `renders\` 可以清（STUDIO 的规矩：源文件永久保留，产物发布后清理）；build 脚本、剪辑表、`brief.md` 留着。
