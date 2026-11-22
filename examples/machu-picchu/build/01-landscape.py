"""Site geometry in local east/north metres. Run with likeness edit new."""
import json
import math
import random
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from modeling import outdoor

ROOT = Path(__file__).resolve().parents[1]
import home
LIB = home.library_root() / "polyhaven"
for _asset in ("leafy_grass", "rock_boulder_dry", "rock_wall_08", "gravel_floor_02", "evening_road_01_puresky"):
    outdoor.library("polyhaven:" + _asset)   # downloads what this machine does not have yet
REF = ROOT / "photos"
PLACE = ROOT / "place"
features = json.loads((PLACE / "features.json").read_text(encoding="utf8"))
dem = np.load(PLACE / "height.npy")
base = json.loads((PLACE / "place.json").read_text())["centre_height"]
rng = random.Random(1912)


def height(x,y):
    ix, iy = np.clip([(1700+x)/3.4, (1700-y)/3.4], 0, 999.99)
    i,j = int(ix), int(iy)
    a,b = ix-i, iy-j
    return float((1-b)*((1-a)*dem[j,i]+a*dem[j,i+1])+b*((1-a)*dem[j+1,i]+a*dem[j+1,i+1]))-base


def mesh(name, vs, faces, mats, ids=None, uv=None):
    m=bpy.data.meshes.new(name)
    m.from_pydata(vs, [], faces)
    m.update()
    o=bpy.data.objects.new(name,m)
    bpy.context.scene.collection.objects.link(o)
    for mat in mats:
        m.materials.append(mat)
    if ids is not None:
        m.polygons.foreach_set("material_index", ids)
    if uv is not None:
        layer=m.uv_layers.new(name="Survey projection")
        indices=np.empty(len(m.loops),dtype=np.int32)
        m.loops.foreach_get("vertex_index",indices)
        layer.data.foreach_set("uv",np.asarray(uv,dtype=np.float32)[indices].ravel())
    return o


def grid(name, nx, ny, sample, mat, use_uv=False):
    vs,uv=[],[]
    for j in range(ny):
        for i in range(nx):
            p,t=sample(i/(nx-1),j/(ny-1))
            vs.append(p)
            if use_uv: uv.append(t)
    fs=[(j*nx+i,j*nx+i+1,(j+1)*nx+i+1,(j+1)*nx+i) for j in range(ny-1) for i in range(nx-1)]
    return mesh(name,vs,fs,[mat],uv=uv if use_uv else None)


def scan(name, asset, metres, tint=(1,1,1)):
    return outdoor.material(str(LIB/asset),metres=metres,tint=tint,relief=0.35,name=name)


def projected(name, path, energy):
    m=bpy.data.materials.new(name)
    m.use_nodes=True
    nodes=m.node_tree.nodes
    tex=nodes.new("ShaderNodeTexImage")
    tex.image=bpy.data.images.load(str(path),check_existing=True)
    tex.extension="EXTEND"
    surf=nodes.new("ShaderNodeEmission")
    surf.inputs["Strength"].default_value=energy
    m.node_tree.links.new(tex.outputs["Color"],surf.inputs["Color"])
    m.node_tree.links.new(surf.outputs[0],nodes.get("Material Output").inputs["Surface"])
    return m


def boxwall(name,a,b,bottom,top,width,mat):
    ax,ay=a;bx,by=b
    d=math.hypot(bx-ax,by-ay)
    nx,ny=-(by-ay)/d*width/2,(bx-ax)/d*width/2
    vs=[(x+side*nx,y+side*ny,z) for x,y in (a,b) for side in (-1,1) for z in (bottom,top)]
    return mesh(name,vs,[(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)],[mat])


def road_z(y):
    return float(np.interp(y,[-100,-53,-51.39,-46.65,-41.5,-36.8,-33.1,-29,-27.51,-24,-21,-13,8,40],
        [22,16.1,15.8,14.65,13.15,11.91,11.55,11.70,11.72,11.5,11.2,10.0,5.2,-1.5]))


def edge_x(y):
    return float(np.interp(y,[-72,-54,-51.39,-46.65,-41.5,-35,-27.51,-13.6,7.7,37],
        [100,76,68.18,62.52,60.72,60.65,60.48,60.4,57.35,54]))


def foreground_z(x,y):
    h=height(x,y)
    distance=abs(x-edge_x(y))
    influence=min(1,max(0,(18-distance)/11))*min(1,max(0,(43-y)/18))*min(1,max(0,(y+89)/15))
    return h*(1-influence)+(road_z(y)-0.18)*influence


grass=scan("Andean turf","leafy_grass",3.2,(0.64,0.83,0.44))
rock=scan("Dressed granite","rock_boulder_dry",2.4,(0.71,0.76,0.72))
wall=scan("Fieldstone courses","rock_wall_08",3.0,(1.14,1.14,1.08))
dirt=scan("Weathered trail bed","gravel_floor_02",2.6,(0.60,0.53,0.42))
ridge_mat=projected("Huayna Picchu | photographic surface",REF/"ridge-at-dusk.jpg",1.08)
far_mat=projected("Cordillera | distant light",ROOT/"build/range-color.jpg",1.03)

# The panorama is projected from the southern terraces. The ridge is a relief surface with
# a traced summit, shoulder and subsidiary peak. Depth falls towards the site at the foot.
profile=[(0,443),(22,399),(48,364),(69,329),(88,306),(107,284),(123,274),(138,271),
 (147,277),(160,294),(174,309),(186,302),(198,292),(208,279),(217,258),(230,230),(244,209),
 (255,195),(268,181),(279,169),(288,164),(299,161),(308,162),(320,168),(328,181),(336,196),
 (345,205),(356,209),(369,227),(384,240),(404,249),(428,252),(453,259),(479,263),
 (505,272),(527,281),(543,300),(561,322),(579,343),(604,356),(635,369)]
foot=[(0,552),(66,475),(115,424),(160,383),(193,358),(215,347),(250,341),(281,347),
 (323,348),(348,351),(401,368),(440,381),(471,396),(508,410),(540,429),(635,429)]
cx,cy,cz=92,-104,30.5
angle=math.radians(7.1)
forward=(-math.sin(angle),math.cos(angle))
right=(forward[1],-forward[0])
def interpolate(points,x):
    return float(np.interp(x,[p[0] for p in points],[p[1] for p in points]))


def projected_position(u,v,d):
    lateral=(u-313)/615*d
    return (cx+forward[0]*d+right[0]*lateral,cy+forward[1]*d+right[1]*lateral,cz+(320-v)/615*d)


def summit(u,t):
    col=u*635
    crest=interpolate(profile,col)
    end=interpolate(foot,col)
    v=crest+(end+145-crest)*t
    depth_top=1003-397*math.exp(-((col-135)/66)**2)+98*math.sin((col-280)/190)
    d=depth_top*(1-0.50*t)+12*math.sin(col*.079+5*t)*math.sin(t*math.pi)
    c=min(v,end-2)
    return projected_position(col,v,d),(col/635,1-c/620)


def distant(u,t):
    col=-500+u*1640
    v=-340+t*940
    d=5700+420*math.sin(col/180)
    return projected_position(col,v,d),((col+500)/1640,1-(v+340)/940)


grid("Cordillera and evening sky",131,48,distant,far_mat,True)
grid("Huayna Picchu",199,43,summit,ridge_mat,True)

# Near the city the DEM is replaced by level platforms, retaining faces and the walking bed.
# The background ridge has its own mesh and is kept clear of the coarse DEM.
def terrain_sample(u,v):
    x,y=-330+u*790,-220+v*589
    z=foreground_z(x,y)-.45
    if -75<y<25 and x>edge_x(y)+2:
        z-=min(14,(x-edge_x(y))*0.55)*min(1,max(0,(25-y)/18))
    return (x,y,z),(0,0)
grid("Citadel ridge | DEM",252,211,terrain_sample,grass)

def road_sample(u,v):
    y=-62+v*94
    return (edge_x(y)+(u-.5)*3.8,y,road_z(y)),(0,0)
grid("Walk surface",3,200,road_sample,dirt)

# Grass platforms continue off the lower side of the approach. Their bends follow the
# southeast slope. The OSM retaining lines beyond them preserve the measured field layout.
def terrace_xy(i,t):
    y=-82+t*88
    x=edge_x(min(-29,y))+2.0 + i*(3.1+0.34*math.sin(t*2.5)) + max(0,y+33)*.93
    return x+1.2*math.sin(2.4*t+i*.14),y
def terrace_z(i,t):
    x,y=terrace_xy(0,t)
    return road_z(min(y,-26))-.18-i*1.19+0.11*math.sin(t*2.3+i*.3)
for i in range(13):
    def bench(u,v,i=i):
        a,b=terrace_xy(i,v),terrace_xy(i+1,v)
        return (a[0]*(1-u)+b[0]*u,a[1],terrace_z(i,v)-u*.045),(0,0)
    def face(u,v,i=i):
        x,y=terrace_xy(i+1,u)
        return (x+(1-v)*.19,y,terrace_z(i,u)-1.35+1.32*v),(0,0)
    grid("Agricultural platform %02d"%(i+1),2,75,bench,grass)
    grid("Agricultural terrace fronts" if i==0 else "Anden retaining face %02d"%i,75,2,face,wall)

# Mapped wall footprints on the ridge. All buildings remain open to the sky, with actual
# wall thickness, sunlit interiors and openings along their principal side.
vs=[];fs=[];ids=[]
def course(a,b,za,zb,thick,kind=0):
    ax,ay=a; bx,by=b; length=math.hypot(bx-ax,by-ay)
    if length<.06:return
    dx,dy=-(by-ay)/length*thick*.5,(bx-ax)/length*thick*.5
    start=len(vs)
    vs.extend([(x+dx*side,y+dy*side,z) for (x,y),z0,z1 in [(a,*za),(b,*zb)] for side in (-1,1) for z in (z0,z1)])
    for face in [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)]:
        fs.append([start+k for k in face]);ids.append(kind)
def along(a,b,t):
    return (a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
for f in features:
    ps=f["points"]
    if len(ps)<2:continue
    x=sum(p[0] for p in ps)/len(ps); y=sum(p[1] for p in ps)/len(ps)
    if not (-120<x<240 and -100<y<340):continue
    if f["kind"]=="wall":
        if f["osm"] in ("way/942151348","way/942151349"):continue
        for a,b in zip(ps,ps[1:]):
            ha,hb=foreground_z(*a),foreground_z(*b)
            course(a,b,(ha-1.2,ha+0.85),(hb-1.2,hb+0.85),.52)
    if f["kind"]!="building" or not f["closed"] or len(ps)>35:continue
    if f["osm"] in {"way/844863650","way/844863649","way/844988214"}:continue
    floor=foreground_z(x,y)
    wall_h=rng.uniform(1.65,3.05)
    lengths=[math.dist(a,b) for a,b in zip(ps,ps[1:]+ps[:1])]
    long=int(np.argmax(lengths))
    for j,(a,b) in enumerate(zip(ps,ps[1:]+ps[:1])):
        za,zb=min(floor-1.35,foreground_z(*a)-1),min(floor-1.35,foreground_z(*b)-1)
        top=floor+wall_h+rng.uniform(-.10,.10)
        if j==long and lengths[j]>3:
            c,d=along(a,b,.34),along(a,b,min(.76,.34+1.12/lengths[j]))
            course(a,c,(za,top),(floor-1.4,top),.55)
            course(d,b,(floor-1.4,top),(zb,top),.55)
            if wall_h>2.1:course(c,d,(floor+1.85,top),(floor+1.85,top),.55)
        elif len(ps)==4 and lengths[j]<max(lengths)*.76 and rng.random()<.58:
            mid=along(a,b,.5)
            course(a,mid,(za,top),(floor-1.4,top+1.70),.57)
            course(mid,b,(floor-1.4,top+1.70),(zb,top),.57)
        else:
            course(a,b,(za,top),(zb,top+rng.uniform(-.18,.18)),.55)
mesh("Urban ensemble",vs,fs,[wall],ids)

gx,gy,gz=60.48,-27.51,road_z(-27.51)
# The blocks here set occlusion, threshold and doorway before the masonry pass.
boxwall("Blockout | left jamb",(gx-3,gy),(gx-.74,gy),gz-.3,gz+3.26,.95,rock)
boxwall("Blockout | right jamb",(gx+.74,gy),(gx+2.8,gy),gz-.3,gz+3.26,.95,rock)
boxwall("Blockout | lintel",(gx-3,gy),(gx+2.8,gy),gz+3.25,gz+4.75,.95,rock)
boxwall("Blockout | passage",(gx+1.5,gy+.3),(gx+1.44,-11),gz-.5,gz+1.28,.65,wall)
boxwall("Blockout | western approach",(gx-2.85,gy),(gx-3.95,gy-10.5),gz-.4,gz+4.15,.85,wall)

light=outdoor.sky(str(next((LIB/"evening_road_01_puresky").glob("*.hdr"))),
    sun_azimuth=199,preset="dusk",sun_elevation=22,sun_strength=4.2,sun_color=(1,.82,.58),
    strength=.48,sun_angle=1.8,disc=0,exposure=.05)
outdoor.film_settings(width=1920,height=1080,samples=32)
bpy.context.scene.world.color=(.33,.38,.43)
bpy.context.scene["Site datum"]="2443.17 m; +X east, +Y north"
