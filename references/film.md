# 出片

从镜头定下来到一条完整的片子。原则：先便宜地确认，再花时间和钱；渲过的帧永远不用为了换规格重渲。

## 顺序

1. **关键位置各一张**（`--frames check`），拼总览图，过评审。
2. **低清整条**：960 宽、低采样把整条渲出来，切段、放进页面滚一遍。看的是节奏、遮挡、过渡和首尾能不能接上。这一步在本机就能做。
3. **估算**：用正式规格渲三五张有代表性的帧，量出每帧多少秒、占多少显存和内存，算出总时间和钱。
4. **正式渲染**：本机或云上。
5. **切段、检查、出各档清晰度。**

## 渲染设置（那次的正式值）

湖那条片子的渲染脚本里的值，Cycles：

| 项目 | 数值 |
|---|---|
| 尺寸 | 2560×1440，每秒 60 帧 |
| 采样 | 128，自适应阈值 0.05，最少 32 |
| 降噪 | OpenImageDenoise，在显卡上跑 |
| 反弹 | 总 6，漫反射 2，光泽 3，透射 6，透明 40，体积 0 |
| 其他 | 关焦散；间接光钳制 8；随机种子固定、不随帧变 |
| 场景常驻 | 打开（连续渲很多帧时不用每帧重新载入） |
| 输出 | PNG，压缩 30 |

种子固定是为了让降噪后残留的颗粒每帧一样，画面不闪。`likeness shoot` 默认这样设。

镜头停稳的那几秒最容易看出闪烁和颗粒，估算时一定挑一张停靠点的帧仔细看。

## 估算

```text
likeness tools hardware
likeness tools estimate walk.blend --frames 0 840 2280 --count 6840
```

- 挑的帧要覆盖最重的画面：树多的、有玻璃的、有水的。
- 本机渲不完或者内存不够时才上云。核显和系统共用内存，设备列表里有它不代表场景放得下。
- 那次每个渲染进程占约 13.5 GB 显存和 24 GB 内存。
- 给用户报方案时说三件事：多久能看到、总共多少钱、在哪里渲。

## 本机渲染

```text
likeness shoot walk.blend tmp/frames --frames 0-6839
```

可以随时中断，再跑同一条命令会跳过已经有的帧。每帧先写临时文件再改名，所以文件夹里不会有写了一半的图。

## 切段和母版

```text
likeness pieces tmp/frames tmp/film --fps 60
likeness movie tmp/film tmp/film.mp4 --fps 60 --height 1080
```

- 每秒一个文件，放在 `film/keep/`，接近无损（H.264，质量 12）。**这是母版**，以后要别的清晰度、别的编码都从它出，不重渲。
- 只有凑满一整秒的帧才会切成段，缺帧的秒数会在结果里列出来。
- `movie` 把母版合成一个普通视频，给用户用播放器直接看。
- 那次母版 114 段共 2.4 GB。帧文件体积更大，母版确认无误后帧可以删。

## 成片检查

```text
likeness filmcheck tmp/film tmp/overview --fps 60 --loop
```

逐帧比较相邻两张的差别，报告突然的跳变（缺帧、渲错的帧、不同机器渲出来的接缝）和循环片子最后一帧到第一帧的差别，并且每两秒存一帧拼成总览图。总览图要自己看一遍：天气和光的过渡、有没有哪一段明显不一样。

## 云上渲染

单台机器用工具箱里的命令，带预算上限、时限和自动释放，见 [单台云机器](rendering.md)：

```text
likeness tools cloud offers
likeness tools cloud render walk.blend tmp/job --frames 0 6839 --gpu "GPU ID" --hours 3 --budget 30 --extra-cost 3 --disk 120 --key ssh-key
```

多台机器同时渲用 `likeness fleet`（`scripts/film/fleet.py`，在机器上跑的是 `fleet_worker.py`）。在一个任务文件夹里写 `fleet.json`：

```json
{
  "runpod_key": "D:/keys/runpod-key.txt",
  "ssh_key": "D:/keys/render_key",
  "cards": ["NVIDIA GeForce RTX 4090", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"],
  "regions": ["EU-RO-1"], "max_rate": 0.95,
  "blender": "5.2.4",
  "upload": {"dl": "D:/proj/dl", "scripts": "D:/proj/scripts", "path.json": "D:/proj/tmp/path.json"},
  "build": "blender -b --python scripts/build_scene.py -- dl scene.blend",
  "test": "blender -b scene.blend --python scripts/render.py -- path.json {frames} 640 8 600,1311,1487",
  "render": "blender -b scene.blend --python scripts/render.py -- path.json {frames} 2560 128 {first}-{last}",
  "frames": [60, 1919], "piece": 60, "fps": 60,
  "hours": 2.5, "budget_usd": 15
}
```

```text
likeness fleet up tmp/job 5        # 租 5 台单卡机：装 Blender 和 FFmpeg，第一台从本机收数据，其余从第一台复制，各自建场景、试渲两遍
likeness fleet go tmp/job          # 按秒分段，轮流分给各台，每台起一个 worker
likeness fleet watch tmp/job       # 取回、给闲下来的机器分活、做完的机器立刻关；全部到齐或超时超预算就全部关机
likeness fleet frames tmp/job tmp/frames    # 取回的段解回 PNG
```

- 渲染命令只要能把 `{first}`..`{last}` 渲成 `{frames}/f_NNNNN.png`（本机 shoot 的五位数）或 `f_NNNN.png` 就行。worker 每段启动一次 Blender，场景很重时把 `piece` 调大（120 帧）摊薄载入时间。
- 每段在机器上编成 RGB 的 H.264，没有色彩换算；默认质量 6，看不出差别但不是逐位一致，要逐位一致写 `"crf": 0`（文件大几倍）。帧随后删掉。2560×1440 质量 6 每段 20 到 70 MB。
- `regions` 指定机房（`likeness tools cloud offers` 按显卡列出各机房的库存），`max_rate` 是单卡每小时上限；租到别处或更贵的机器当场退掉。不写 `regions` 时 Runpod 自己挑机房，先用 offers 看清楚再告诉用户机器在哪。
- `hours` 和 `budget_usd` 是整个任务的硬上限：从第一次租机算起，算上这个任务租过的每一台（包括试渲失败退掉的、连不上的），磁盘也按 Runpod 标价计入。第一次租机时会在后台起一个看守进程，到点或超预算就把这个任务的机器全部退掉，并向 Runpod 核实确实没了；`up`、`watch` 停了、会话断了它也照样执行。账目在任务文件夹的 `leases.json`，`likeness fleet stat` 随时报花了多少、还剩多少分钟。
- 机器之间复制数据 5 分钟还没到，就改从本机直接传；SSH 失败会把原因写进 `fleet.log`。Windows 上私钥会先复制一份只有当前用户能读的到 Likeness 的 home 里再用，OpenSSH 才肯用它。
- 2026-11-22 马丘比丘那次用 3 台罗马尼亚的 RTX 4090 渲完 1920×1080 的 1440 帧，实付 1.89 美元；上面几条就是那次补上的。

做法要点：

- **分段发活**：片子按秒分段，相邻的段分给不同机器，片子各处同时推进，中途停掉也有一整条稀疏的可看。
- **边渲边编码**，**取回、核对、关机**：本机定时取回；某台机器的队列空了、它做的段都已在本机，就立刻关掉；全部到齐后关掉所有机器，并列出账号里还开着的机器。
- **掉线接管**：机器连续不应答就关掉，它没做完的段转给别的机器。一段刚被领走、还没出第一帧时看起来像“丢了”，要连续两次查不到才算丢，否则会重复渲。
- **时限和预算**：超过设定的小时数或估算花费，取回已有的、全部关机。

那次的账：

| | 机器 | 时间 | 花费 |
|---|---|---|---|
| 第二条片子（6540 帧） | 6 台单卡：2 台 RTX PRO 6000、4090、L40S、3090、A40 各 1 台 | 约 2 小时 | 31.54 美元，其中约 9 美元是渲完后空转了一小时 |
| 第三条片子（6840 帧） | 8 台 | 2 小时 5 分 | 24.4 美元，含搭场景和试渲 |

教训，每条都照做：

1. **先查实际库存再报方案。** 那次说了“4 块 RTX 5090”，结果当时只有零散的单卡机器，也没有想要的机房。先 `cloud offers` 看真实情况，报方案时说清楚在哪个地区渲。
2. **渲完自动关机，不靠人记着。** 第一次没有安排，空转了一小时。
3. **花钱之前说清范围**：总额上限、最长时间、哪些文件会离开本机。实际做法和说好的不一样时，先说再做。
4. **密钥只放在本机的文件里**，不进命令行、不进日志、不进上传的包。用完提醒用户到服务商那里删掉。
5. **场景在云机器上重建**（那次约 4 分钟），比上传 2.4 GB 的场景文件快；素材和脚本传上去即可。
6. **不同型号的显卡渲出来的段之间没有看得出的接缝**（那次逐帧查过），但换型号时仍然比一张交界帧。
7. **先用一台便宜的机器搭场景、反复试渲和改画面**，画面定了再租一批渲正片。
8. **新机器先试渲再用。** 2026-11 那次有一台 RTX 4090 一半的渲染报 “Illegal address in CUDA queue”，换 OptiX、CUDA、Blender 4.4、4.5 都一样；同型号、同驱动的另一台机器完全正常。是那台机器的问题：试渲几帧、跑两遍，出错就立刻关掉换一台，不要花时间排查。`likeness fleet up` 已经这样做。
9. **远程停进程别误杀自己。** `ssh 机器 "pkill -f render.py; blender ... render.py"` 会把这条 ssh 命令本身也杀掉，因为它的命令行里也有 render.py。停和启分两次 ssh。
10. **随时间变化的几何（几何节点做的雪、雨）每帧都要重建**，那次每帧多 3 秒。只在它出现的那一段打开，其余帧设成不渲染。
11. **要真实运动模糊，镜头必须逐帧打关键帧**（只在要渲的帧前后各一帧也行），渲染时用 `frame_set` 换帧；每帧直接改镜头位置是没有模糊的。

