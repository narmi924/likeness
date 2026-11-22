---
name: likeness
description: 用 Blender 把真实的地方、建筑、产品和物件照着照片、地图和高程做成 3D，并一路做到能直接用的成品：单张图、转台、走进去的片子、网页里滚动就能往前走的片子、能放进网页的模型。先定成品给人怎么看，再围着镜头去建，每一轮都渲染出来看图再改。用户说“做个 3D 场景”“把这个地方/这栋楼/这个产品建出来”“给网站做 3D”“做一段漫游”“渲染”“Blender”时使用。Use for 3D modelling and scenes in Blender, replicas of real places, buildings and products from photographs, walk-through films, scroll-driven 3D for websites, turntables, renders, GLB export, and planning local or cloud rendering.
---

# Likeness

把一个真实的地方或一件东西做成 3D，像到一眼认得出，一直做到别人能直接看、直接用。方法来自一次真实的工作：一个网站首页，访客滚动鼠标绕湖走一圈，114 秒、6840 帧、2560×1440、每秒 60 帧，经过四个真实的地方。做法和踩过的坑都在这里。

所有命令都通过一个入口运行，在任何文件夹里都能用：

```text
python <skill>/scripts/likeness.py setup        # 第一次用：缺什么、装在哪；加 --get 自动下载 Blender、FFmpeg 和起步素材
python <skill>/scripts/likeness.py where        # 本机的 Blender、FFmpeg、素材库在哪
python <skill>/scripts/likeness.py --help       # 真实地方、俯视图、路线和成片的命令
python <skill>/scripts/likeness.py tools --help # 建模工具箱的命令
```

下文把它简写成 `likeness`。第一次在一台电脑上用，先跑 `likeness setup`，报缺什么就 `likeness setup --get`，下载前告诉用户要下多大、放哪。下载的东西都放在 home 文件夹（`likeness where` 显示），不放进 skill 文件夹。本机装过的 Blender、FFmpeg、Python 直接用：不要为 Likeness 另建 Python 虚拟环境、改 PATH 或复制 Blender，`setup` 报缺的才装。

本机的设置写在 skill 文件夹里的 `local.json`（不随 skill 分享）：`{"home": "E:/likeness", "library": "...", "blender": "..."}`，都可以不写。

## 一、先定成品给人怎么看

动手建模之前先回答这个问题，它决定了后面所有的取舍。选错形式，后面修多少轮都救不回来。详见 [定形式](references/form.md)。

| 用户要的 | 用什么形式 | 关键 |
|---|---|---|
| 一张好看的图 | 单张渲染 | 机位和光在粗模阶段就定 |
| 把一件东西看个遍（产品、物件） | 转台，可拖动旋转 | 背面、底面、接缝都要经得起看 |
| 走进一个地方，照片级，任何设备都流畅 | **离线渲染的片子，滚动就是拖片子** | 围着镜头路线建，见第四节 |
| 访客要自由走动，或内容要实时变化 | 实时 3D（导出 GLB） | 画面上限低，弱设备会卡，先说清代价 |
| 放进视频里 | 渲染成片 | 同上，按镜头建 |
| 给已经做好的 3D 片子或页面做宣传片 | 回原场景用新镜头重拍 | 不加工已有成片的像素；界面当贴图渲进场景，镜头卡音乐的落点，见 [宣传片](references/promo.md) |

用户说“实时 3D 场景”但真正要的是“好看、流畅、像真的”时，直接推荐片子，并说明代价：改一处场景要重渲那一段。

## 二、每件作品都按这个顺序做

1. **弄清要认出什么。** 最近看到多近、多大尺度、哪些特征必须一眼认出。照片先用用户给的，缺的角度自己找（`python <skill>/scripts/sources.py search commons "<地名> <部位>"`，再 `fetch`；网上搜到的也行），按方位和位置归好类再看。找一个已知尺寸定比例（门高、层高、台阶、产品规格）。真实的地方先用 `likeness place <纬度> <经度> <文件夹>` 拉下高程和 OpenStreetMap 的轮廓，得到一张带米数网格的地图，照片里的东西都对到这张图上。
2. **画俯视图，停下来等用户确认。** 开建之前必做，用户在这一步改方向最便宜。走进去的片子先写一版 `route.json`（路线、停靠点、每处看什么），用 `likeness plan route.json plan.png --place <place 文件夹> --lang zh` 画在真实地图上（左边是整个地方、右边是路线细部，下面每个停靠点一张卡片：几秒到、要拍到什么；停靠点的 key 里写 `note` 就用这句话）；单张图、转台也画一张，标出机位和主体。把图给用户，说清每个停靠点要认出什么、全程多少秒，**用户回复之前不开建**。
3. **粗模、机位、光一起定。** 用最便宜的体块把大形、比例、镜头和光的方向摆出来。此时就用成品的机位看，不用别的漂亮角度。
4. **渲染出来看图，再改。** 每一轮只改画面里最显眼的那个问题。怎么看、改什么，见 [建模](references/modeling.md) 和 [好看和真实](references/look.md)。改前改后用同一个机位、同一帧、同样的光对比。
5. **请只看画面的评审。** 自己看不出问题时不算完。另起一个不知道制作过程的评审，只给它图，让它挑毛病，照着改，至少两轮。做法见 [好看和真实](references/look.md#看图把关)。
6. **出成品之前先量再渲。** 估时间、估内存、估钱，再决定本机还是云上。见 [出片](references/film.md)。
7. **交付。** 成品、能再打开修改的 `.blend`、生成它的脚本、素材来源各一行（`likeness place` 会写好地图和高程的来源）。

建模和看图的基本命令（场景用 bpy 脚本搭，每一步存成新文件）：

```text
likeness edit new build/01-blockout.py tmp/01.blend           # 从空场景跑一个脚本，存成新文件
likeness edit tmp/01.blend build/02-shape.py tmp/02.blend     # 在上一版上接着改
likeness view tmp/02.blend tmp/02.png --camera "Review" --style render    # 渲一张看。--style clay 只看形，--angle sheet 四个角度
likeness compare tmp/02.blend references/a.jpg tmp/cmp.png --angle camera --camera "Review"   # 和参考图并排
likeness inspect tmp/02.blend                                 # 场景里有什么、叫什么名字
```

## 三、户外场景从这里起步

不要从空场景开始摸索。`examples/starter/` 是一个能直接跑的小场景：按真实米数铺的扫描地面、碎石路、手工摆的扫描树和石头、路边的草、远处的树带、黄昏的光和空气。效果见同目录的 `preview.jpg`。把 `build.py` 和 `route.json` 复制到项目里再改。

```python
from modeling import outdoor, terrain
lib = outdoor.library                                    # lib("polyhaven:island_tree_01")：本机素材库里的路径，`likeness library` 列出全部
ground = terrain.ground(size=(160, 160), resolution=200, height=1.4)
outdoor.ground(ground, lib("polyhaven:leafy_grass"), patches=lib("polyhaven:forest_leaves_02"),
               path=lib("polyhaven:gravel_floor_02"), path_line=PATH, path_width=2.6)        # 地面：贴图按米数从上往下铺
outdoor.plant(lib("polyhaven:island_tree_02"), [(22, 12, 11), (27, 4, 9)], size=10, ground_object=ground)   # 摆扫描模型
outdoor.strew(ground, [(lib("polyhaven:grass_medium_02"), 0.9)], density=1.3)               # 只在路边撒草
light = outdoor.sky(lib("polyhaven:evening_road_01_puresky"), sun_azimuth=115)              # 天空图加一盏对齐的太阳灯，默认黄昏
outdoor.haze(light)                                                                         # 远处变淡，所有材质建完后调用
outdoor.film_settings(width=1920, samples=96)                                               # 那次成片的渲染设置
```

有水、有岸、有远山的地形，用一个高度函数直接生成，水面是一张放在水位上的平面：

```python
ground = outdoor.land((-150, -70, 150, 210), height, cell=0.9)      # height(x, y) -> z，自己写：近岸、湖底、对岸、远山
outdoor.ground(ground, lib("polyhaven:leafy_grass"), path=..., path_line=PATH, tint=(0.5, 0.66, 0.36), path_tint=(0.6, 0.56, 0.5),
               shore=lib("polyhaven:coast_sand_rocks_02"), water_level=0.0)   # 草调绿、碎石压暗、水边一圈湿沙
outdoor.water(level=0.0)                                             # 静水：映着天和岸，浅处看得到水底
outdoor.plant(lib("polyhaven:fir_sapling_medium"), spots, size=14, ground_object=ground, group="Far wood")   # 一片树林归到一个名字下
```

真实的地方用 `likeness place` 拉下的高程和轮廓，和地图、俯视图是同一套米数：

```python
place = outdoor.place("<place 文件夹>")      # x 向东，y 向北，原点在地图中心；高度以中心点为 0（place.base 是它的海拔）
near = (-60, -260, 60, -140)                 # 镜头经过的一块，细做
inside = lambda x, y: near[0] < x < near[2] and near[1] < y < near[3]
outdoor.land(place.extent, lambda x, y: place.height(x, y) - (0.4 if inside(x, y) else 0), cell=4, name="Far land")
ground = outdoor.land(near, place.height, cell=0.5)
for f in place.outlines("building"): f["points"]        # OpenStreetMap 的房屋轮廓 [[x, y], ...]；还有 wall、path、area 等
```

高程大约 30 米一个点，梯田、墙、崖壁、台阶都不在里面，要照着照片在上面建。OpenStreetMap 的轮廓定位置和朝向，高度、层数、石头的样子看照片。

城镇、天气和远景有现成的：

```python
from modeling import city, weathering as W
homes = city.facade("Housing", "housing", floor_h=3.0, bay=3.6)      # 窗、窗框、窗台、半拉的窗帘、一部分亮灯
glass = city.facade("Offices", "curtain", floor_h=3.8, bay=1.8)      # 整层玻璃幕墙；"stone" 是石柱夹玻璃
city.buildings(place.outlines("building"), place.height, [homes, glass], keep=lambda x, y: abs(x) > 60)   # 按地图轮廓和标注的层高立起来
city.skyline((0, 0), 500, 1400, 300, place.height, [homes, glass], bearing=(20, 110))                     # 地平线上的一片城
city.lights(0.8)                                       # 所有亮着的窗一起调，白天 0
W.cover(ground.active_material, snow=0.7)              # 朝上的面积雪；rain=1 是湿地面和水洼；above=2600 只在这个高度以上
W.streaks(wall_material, 0.4)                          # 墙上的雨痕和墙脚的脏
far = W.mountain(zmax=1800, snowline=0.36)             # 远山：冲沟、岩石、林带、雪线
winter = W.bare(tree)                                  # 落了叶的树；W.snowy(fir) 挂雪的针叶；W.leaves(tree, hue=0.52) 调叶色并透光
keep = outdoor.clear_view(stop_xy, landmark_xy, far=120)   # 停靠点看向地标的那个锥形里不种东西
spots = outdoor.scatter((-80, -80, 80, 80), 200, keep=[keep], spacing=4)
```

这些楼只用在中远景：轮廓、高度、窗的节奏和几盏灯是那里看得出的全部。镜头几米内的楼照照片一栋一栋建。

自己建的东西（木板、桩、墙、船壳）用 `outdoor.material(lib("polyhaven:wood_planks_grey"), metres=1.4, tint=(0.86, 0.84, 0.8))` 上材质：不需要展 UV，贴图按米数从三个方向包上去，每个物体深浅略有不同。

`outdoor.haze` 只包当时已经存在的材质，包过的不会重复包。后来又加了东西，再调一次 `outdoor.haze(light)`。`outdoor.sky` 可以重复调用来换光。

每个函数的参数见 `scripts/modeling/outdoor.py`。建筑、产品等其他模块见 [建模](references/modeling.md)。

## 四、走进去的片子

镜头沿一条路线走，停在几个地方。整个世界围着这条路线建：镜头看得到的地方花力气，看不到的不建。详见 [路线和布景](references/route.md)。

```text
likeness walk route.json walk.json --ground scene.blend --poses poses.json   # 路线 -> 每一帧的镜头
likeness bake scene.blend walk.json walk.blend                               # 镜头写进场景，时间轴上能直接拖着看
likeness sight walk.blend walk.json                                          # 每个停靠点该看到的东西在不在画面里、有没有被挡
likeness shoot walk.blend tmp/drafts --frames check --walk walk.json --jpg --width 960   # 关键位置各渲一张
likeness sheet tmp/drafts.jpg tmp/drafts --walk walk.json                    # 拼成一张总览图，自己看，也给评审看。文件夹里帧很多时加 --frames check 只拼关键位置
likeness shoot walk.blend tmp/frames --frames 0-239                          # 正式帧，断了可以接着渲（已有的帧跳过；要重渲加 --again）
likeness pieces tmp/frames tmp/film --fps 60                                 # 每秒一段的母版，以后换规格不用重渲
likeness web tmp/film tmp/site walk.json --scene walk.blend                  # 网页用的各档清晰度、film.json 和播放页
likeness filmcheck tmp/site tmp/overview                                     # 逐帧查跳变和首尾接缝，出总览图
likeness serve tmp/site                                                      # 本地打开，滚动走一遍。放到后台运行，看完停掉；或加 --for 120 让它两分钟后自己停
likeness fleet up tmp/job 5                                                  # 多台云显卡一起渲：up 租机器并试渲，go 分段开渲，watch 取回并关机，frames 解回 PNG。见 references/film.md
likeness page shot "http://127.0.0.1:8765/?debug" tmp/page.png --run "walk.setAuto(true)" --wait 8
```

`route.json` 的写法在 `scripts/film/route.py` 开头。放进网站的做法、滚动手感、清晰度、声音和天气，见 [放进网页](references/web.md)。

## 五、最容易错的地方

这些每一条都返工过，动手前读一遍。完整的清单在各篇参考里。

- **画面平，先怀疑模型、材质和光。** 同一份模型换渲染器也一样平。
- **近处用真东西。** 镜头几米内的树、石头、地面用扫描素材和真实几何。程序生成的楼只放远处，近处会显得粗糙。
- **不要为了遮挡造地形。** 孤零零的土包一眼假。要挡东西，用种树和镜头朝向。
- **每个停靠点先列出必须入画的东西**，开渲前用 `likeness sight` 查。一棵近处的树能把地标挡掉大半程。
- **主体放在画面中间偏内**，不要只在边上露一下。首帧要是全片最好的一张。
- **拍建筑时镜头基本放平**，用镜头上移（`shift`）把楼顶收进来，竖线才不会歪。
- **走路的速度。** 人散步约每秒 1.3 米。湖那次每秒 3.2 米，显得赶。
- **长渲染之前**先渲关键帧、过评审，再渲整条。云机器渲完必须自动关机，并且先核实真的租得到再报方案。
- **雪、雨、沙这类一直在动的东西不要渲进片子**，由网页实时叠加，停下来时还在下，往回滚也不会倒着飘。普通视频（宣传片）正相反：天气要渲进去，开渲前把每个地方的天气列全，沙尘也算。
- **流畅优先于加载时间。** 首次打开先取好几秒再显示。

使用者自己的固定偏好记在 `references/taste.md`。这个文件只在本机，不随 skill 分享：有就在给用户做东西之前读，没有就跳过；用户说出一条以后都要照做的偏好时，记进去。

## 六、文件放哪

- 留下来的东西放 `<项目>/3d/<作品>/`：`<作品>.blend`、`build/` 里按顺序编号的脚本、`references/` 里选定的参考图、`route.json`、`sources.md`（每个外来素材一行：作者、许可、网址）。没有项目文件夹时放 home 文件夹下的 `projects/<作品>/`（`likeness where` 显示 home 在哪）。
- 过程文件放 `<项目>/tmp/<作品>/`：草稿、帧、总览图、成片分段。不往 skill 文件夹和 C 盘写。
- 改场景永远存成新文件，`likeness` 的命令不会覆盖已有文件。认可的版本用 `likeness edit … --promote`。
- 文件按轮次编号：`build/03-scene.py` 对应 `tmp/03.blend`、`tmp/03w.blend`（写进镜头之后）、`tmp/d3/`（草稿）。命令报“文件已存在”时换下一个编号重跑，**不要接着用旧文件往下做**，否则渲出来的是上一轮的场景。
- 用了扫描素材的场景，每存一版就是 0.5 到 1 GB（贴图存在文件里）。新一轮确认比上一轮好之后，把更早几轮的 `.blend` 删掉，脚本留着就能重建。
- 脚本用写文件的工具保存后再运行，不要用命令行的多行输入拼脚本（引号和中文会出错）。

## 七、参考

- [定形式](references/form.md)：实时还是片子，各自的代价和数字。
- [路线和布景](references/route.md)：路线文件、停靠点、围着路线摆东西。
- [好看和真实](references/look.md)：光、天空、材质、素材，以及看图把关的做法。
- [出片](references/film.md)：渲染设置、估算、分段、云上多台机器（`likeness fleet`）。
- [宣传片](references/promo.md)：从做好的场景里再拍一支卡点的短片，网页界面当贴图渲进去。
- [放进网页](references/web.md)：播放器、滚动手感、清晰度、声音、天气、托管。
- `references/taste.md`：本机使用者的固定偏好（只在本机，可能没有）。
- [建模](references/modeling.md)：从观察建模、参考和素材来源、建筑、产品、环境、人物（英文）。
- [Blender 的坑](references/pitfalls.md)、[单台云机器](references/rendering.md)、[贴图提示词](references/image-prompts.md)（英文）。
- `examples/starter/`：户外场景的起点。`examples/jetty/`：黄昏走向湖边栈桥的完整脚本和路线。`examples/machu-picchu/`：真实地方的完整做法，地图、高程、照片投影、照着照片描出的石门，一路到云渲染和网页。本机有 `examples/lakeside/`（湖那次的完整脚本、云渲染和网页代码）和 `examples/promo/`（宣传片）时可以去查原做法，它们不随 skill 分享。

会话里如果有 `show_model`、`locate_surface`、`find_references` 这几个工具，可以用它们在画面上取点和找素材；没有就用 `likeness view` 和 `likeness locate`，效果一样。
