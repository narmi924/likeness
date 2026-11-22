"""Fit the field stones to the traced jambs and lintel with shared joint boundaries."""
from pathlib import Path
import json
import random
from shapely import MultiPoint, Polygon, voronoi_polygons, union_all, affinity

here=Path(__file__).resolve().parent
j=json.loads((here/"gate-joints.json").read_text())
def world(p):
    x,y=p
    return ((x-510+(1255-y)*.027)/242,(1255-y)/242)
traced=[Polygon([world(p) for p in ring]).buffer(0) for ring in j["left_tiles"]+j["right_tiles"]+[j["lintel"]]]
frame=Polygon([(-2.96,-.19),(-.88,-.19),(-.75,3.25),(.79,3.25),(.91,-.19),
               (2.8,-.19),(2.8,4.91),(2.2,4.94),(.5,4.87),(-1.0,4.92),(-2.96,4.97)])
region=frame.difference(union_all(traced))
r=random.Random(891)
seeds=[(-3.2+(x+r.uniform(-.34,.34))*.61,(-.5+(y+r.uniform(-.32,.32))*.365)*1.5)
       for x in range(12) for y in range(17)]
cells=voronoi_polygons(MultiPoint(seeds))
stones=[]
for cell in cells.geoms:
    piece=affinity.scale(cell,1,1/1.5,origin=(0,0)).intersection(region)
    for part in getattr(piece,"geoms",[piece]):
        if part.geom_type=="Polygon" and part.area>.014:
            stones.append([[round(a,5),round(b,5)] for a,b in list(part.exterior.coords)[:-1]])
(here/"gate-infill.json").write_text(json.dumps(stones,separators=(",",":")),encoding="utf8")
