"""A starter outdoor scene in the manner of the lakeside film: scanned ground laid in real metres, a gravel path,
scanned trees and rocks placed by hand, grass along the path, dusk light, haze. Copy it into a project's build/ folder
and change it; the same PATH goes into route.json as "line".

    likeness edit new build.py tmp/starter.blend
    likeness walk route.json tmp/walk.json --ground tmp/starter.blend
    likeness bake tmp/starter.blend tmp/walk.json tmp/walk.blend
    likeness sight tmp/walk.blend tmp/walk.json
    likeness shoot tmp/walk.blend tmp/drafts --frames check --walk tmp/walk.json --jpg --width 960"""
import random

from modeling import outdoor, terrain

random.seed(7)
lib = outdoor.library
PATH = [[-34, -9], [-20, -4], [-8, 1], [4, 3], [16, 7]]

# ---- ground: 0.8 m cells, gentle relief; grass with leaf litter in patches, gravel on the path
ground = terrain.ground(size=(160, 160), resolution=200, height=1.4, seed=4)
outdoor.ground(ground, lib("polyhaven:leafy_grass"), size=3.0, patches=lib("polyhaven:forest_leaves_02"),
               path=lib("polyhaven:gravel_floor_02"), path_line=PATH, path_width=2.6)

# ---- what the walk arrives at: two trees round a group of rocks, placed by hand because they are the picture
outdoor.plant(lib("polyhaven:island_tree_02"), [(22, 12, 11), (27, 4, 9)], size=10, ground_object=ground, seed=1, name="Feature tree")
outdoor.plant(lib("polyhaven:coast_land_rocks_02"), [(20, 8)], size=4.5, ground_object=ground, seed=2, name="Rocks")


# ---- the rest: trees well clear of the path and of the line of sight to the rocks, then small things near the lens
def clear_of_view(x, y):
    """Not within 5 m of the path, and not between the last stretch of the path and the rocks."""
    for (ax, ay), (bx, by) in zip(PATH, PATH[1:]):
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
        if ((x - ax - t * (bx - ax)) ** 2 + (y - ay - t * (by - ay)) ** 2) ** 0.5 < 5.0:
            return False
    return not (2 < x < 24 and 0 < y < 12)


spots = []
while len(spots) < 26:
    x, y = random.uniform(-60, 60), random.uniform(-45, 45)
    if clear_of_view(x, y) and all((x - s[0]) ** 2 + (y - s[1]) ** 2 > 36 for s in spots):
        spots.append((x, y, random.uniform(7, 12)))
outdoor.plant(lib("polyhaven:island_tree_01"), spots[:13], size=10, ground_object=ground, seed=3, name="Tree A")
outdoor.plant(lib("polyhaven:tree_small_02"), spots[13:], size=8, ground_object=ground, seed=4, name="Tree B")
outdoor.plant(lib("polyhaven:namaqualand_stones_01"), [(-22, -1.5), (-3, 5.5), (9, 0.5)], size=1.6, ground_object=ground, seed=5, name="Stones")

# far trees all round, so that the horizon is never a bare line
belt = []
while len(belt) < 90:
    x, y = random.uniform(-150, 150), random.uniform(-150, 150)
    if 62 < (x * x + y * y) ** 0.5 < 78:
        belt.append((x, y, random.uniform(9, 14)))
outdoor.plant(lib("polyhaven:island_tree_01"), belt[:45], size=10, ground_object=ground, seed=8, name="Far tree A")
outdoor.plant(lib("polyhaven:tree_small_02"), belt[45:], size=8, ground_object=ground, seed=9, name="Far tree B")

# grass and ferns only along the path, where the lens passes within metres of them
outdoor.strew(ground, [(lib("polyhaven:grass_medium_02"), 0.9), (lib("polyhaven:fern_02"), 0.8)], density=1.3, scale=(0.5, 1.4), seed=6)

# ---- light and air: dusk, the sun low to the left of the walk so that shadows fall across the path; then haze over
# every material
light = outdoor.sky(lib("polyhaven:evening_road_01_puresky"), sun_azimuth=115)
outdoor.haze(light, density=0.0004)
outdoor.film_settings(width=1920, samples=96)
