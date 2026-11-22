"""Reconstruct the far city as a photographed relief and complete the near trail banks."""
import math
import random
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector
from modeling import outdoor

root=Path(__file__).resolve().parents[1];rng=random.Random(296)
import home
LIB = home.library_root() / "polyhaven"
for _asset in ("rock_boulder_dry", "namaqualand_stones_01", "evening_road_01_puresky"):
    outdoor.library("polyhaven:" + _asset)   # downloads what this machine does not have yet
def obj(name):return bpy.data.objects[name]
def mat(name):return bpy.data.materials[name]
def mesh(name,vertices,faces,materials,ids=None,uv=None):
    m=bpy.data.meshes.new(name);m.from_pydata(vertices,[],faces);m.update()
    for a in materials:m.materials.append(a)
    if ids is not None:m.polygons.foreach_set("material_index",ids)
    if uv is not None:
        layer=m.uv_layers.new(name="Site projection");ls=np.empty(len(m.loops),np.int32)
        m.loops.foreach_get("vertex_index",ls);layer.data.foreach_set("uv",np.array(uv,np.float32)[ls].ravel())
    o=bpy.data.objects.new(name,m);bpy.context.scene.collection.objects.link(o);return o
def interp(p,x):return float(np.interp(x,*zip(*p)))
def grid(nx,ny):return [(j*nx+i,j*nx+i+1,(j+1)*nx+i+1,(j+1)*nx+i) for j in range(ny-1) for i in range(nx-1)]
az=math.radians(7.1);co,si=math.cos(az),math.sin(az)
def project(u,v,d):
    w=(u-313)/615*d
    return (92-si*d+co*w,-104+co*d+si*w,30.5+(320-v)/615*d)
def invert(p):
    x,y,z=p;dx,dy=x-92,y+104;d=max(.01,-si*dx+co*dy)
    return 313+615*(co*dx+si*dy)/d,320-615*(z-30.5)/d
far=mat("Cordillera | distant light")
next(n for n in far.node_tree.nodes if n.type=="TEX_IMAGE").image=bpy.data.images.load(str(root/"build/valley-color.jpg"))
# Register the extended outer frame to the same angular calibration as the centre.
outer=obj("Cordillera and evening sky").data
outer_uv=[]
for v in outer.vertices:
    u,h=invert(v.co)
    u=-700+(u+500)/1640*2310;h=-470+(h+340)/940*1520
    d=5700+420*math.sin(u/180)
    v.co=project(u,h,d);outer_uv.append(((u+700)/2310,1-(h+470)/1520))
outer_ls=np.empty(len(outer.loops),np.int32);outer.loops.foreach_get("vertex_index",outer_ls)
outer.uv_layers.active.data.foreach_set("uv",np.asarray(outer_uv,np.float32)[outer_ls].ravel())
summit=obj("Huayna Picchu")
foot=[(0,543),(66,464),(115,410),(160,371),(193,346),(215,338),(250,333),(281,335),
      (323,336),(348,338),(401,354),(440,368),(471,382),(508,396),(540,414),(635,417)]
uv=[]
for v in summit.data.vertices:
    u,h=invert(v.co);lower=interp(foot,u)
    if h>lower:h=lower-abs(23*math.sin((h-lower)/35))-.06*(h-lower)
    uv.append((u/635,1-h/620))
ls=np.empty(len(summit.data.loops),np.int32);summit.data.loops.foreach_get("vertex_index",ls)
summit.data.uv_layers.active.data.foreach_set("uv",np.array(uv,np.float32)[ls].ravel())

# The citadel relief is divided into depth rows at the plaza, ceremonial sector, houses
# and quarry. Individual mapped walls and gables below the surface still give parallax.
crest=[(0,545),(37,520),(67,476),(92,450),(119,421),(149,393),(178,365),(199,355),
       (222,349),(248,345),(278,344),(309,348),(337,349),(367,353),(396,362),
       (425,373),(455,385),(484,398),(505,411),(531,427),(558,429),(600,429),(635,429)]
depths=[(336,446),(350,378),(366,321),(382,277),(399,227),(424,181),(446,157),(467,132),(495,111),(540,86),(585,66)]
city=mat("Huayna Picchu | photographic surface").copy();city.name="City terraces | survey colour"
nodes=city.node_tree.nodes
next(n for n in nodes if n.type=="EMISSION").inputs["Strength"].default_value=.89
vs=[];uv=[]
disp=np.load(root/"build/citadel-disparity.npy")
nx,ny=305,98
for j in range(ny):
    t=j/(ny-1)
    for i in range(nx):
        u=25+565*i/(nx-1)
        top=interp(crest,u);v=top+(max(498,top+30)-top)*t
        iy=int(np.clip(v/620*(disp.shape[0]-1),0,disp.shape[0]-1))
        ix=int(np.clip(u/635*(disp.shape[1]-1),0,disp.shape[1]-1))
        centre=285-187*t**.83
        depth=float(np.clip(298/max(.25,disp[iy,ix]-.535),centre*.84,centre*1.17))
        x,y,z=project(u,v,depth)
        x-=25;y+=45;z-=6.7
        z-=max(0,t-.85)**2*600
        vs.append((x,y,z));uv.append((u/635,1-v/620))
mesh("Upper city | plaza, gables and quarry",vs,grid(nx,ny),[city],uv=uv)
# Extend the photographed slopes over the coarse DEM where the ground breaks through
# the skyline or the urban relief. Use the same metric reference as the city's surface.
ground=obj("Citadel ridge | DEM").data
ground.materials.append(city)
lay=ground.uv_layers.new(name="Survey projection")
for poly in ground.polygons:
    x,y,z=poly.center
    if y<6 and abs(x-60)<55:continue
    u,v=invert((x+25,y-45,z+8.7))
    if not (2<u<633 and 315<v<617):continue
    poly.material_index=1
    for li in poly.loop_indices:
        x,y,z=ground.vertices[ground.loops[li].vertex_index].co
        u,v=invert((x+25,y-45,z+8.7))
        lay.data[li].uv=(u/635,1-v/620)
urban=obj("Urban ensemble").data
urban.materials.append(city)
layer=urban.uv_layers.new(name="Site projection")
for poly in urban.polygons:
    if poly.center.y<105:continue
    u,v=invert(poly.center)
    if not (30<u<583 and interp(crest,u)-4<v<475):continue
    poly.material_index=len(urban.materials)-1
    for li in poly.loop_indices:
        u,v=invert(urban.vertices[urban.loops[li].vertex_index].co)
        layer.data[li].uv=(u/635,1-v/620)

# A stepped trail has filled shoulders at its high edge and eroded soil between flags.
def road(y):
    return float(np.interp(y,[-100,-53,-51.39,-46.65,-41.5,-36.8,-33.1,-29,-27.51,-24,-21,-13,8,40],
      [22,16.1,15.8,14.65,13.15,11.91,11.55,11.70,11.72,11.5,11.2,10,5.2,-1.5]))
def edge(y):
    return float(np.interp(y,[-72,-54,-51.39,-46.65,-41.5,-35,-27.51,-13.6,7.7,37],
      [100,76,68.18,62.52,60.72,60.65,60.48,60.4,57.35,54]))
grass=mat("Andean turf"); wall=mat("Fieldstone courses")
for side in (-1,1):
    vs=[]
    for j in range(170):
        y=-66+j/169*82;x=edge(y)+1.58*side;z=road(y)
        vs.extend([(x,y,z+.015),(x+side*(1.8+.35*math.sin(y*.37)),y,z-.17),
                   (x+side*3.05,y,z-.60),(x+side*4.5,y,z-2.1)])
    mesh("Earth and sod | trail shoulder",vs,grid(4,170),[grass])

# Weathering changes a stone's value gradually. Keep lichen as an inclusion rather
# than letting it turn scattered blocks black.
for mname,fac in [("Lichen crust / granite",.23),("Gate face study / granite microrelief",.76)]:
    m=mat(mname);nt=m.node_tree;bs=nt.nodes["Principled BSDF"]
    orig=bs.inputs["Base Color"].links[0].from_socket
    if "Lichen" in mname:
        granite=nt.nodes.new("ShaderNodeTexImage");granite.image=bpy.data.images.load(str(LIB/"rock_boulder_dry/Diffuse.jpg"),check_existing=True)
        granite.projection="BOX";granite.projection_blend=.23
        mp=next(n for n in nt.nodes if n.type=="MAPPING");nt.links.new(mp.outputs[0],granite.inputs[0])
        mix=nt.nodes.new("ShaderNodeMixRGB");mix.inputs[0].default_value=fac
        nt.links.new(granite.outputs[0],mix.inputs[1]);nt.links.new(orig,mix.inputs[2]);nt.links.new(mix.outputs[0],bs.inputs["Base Color"])
    else:
        hsv=nt.nodes.new("ShaderNodeHueSaturation");hsv.inputs["Value"].default_value=1.48;hsv.inputs["Saturation"].default_value=.83
        nt.links.new(orig,hsv.inputs["Color"]);nt.links.new(hsv.outputs[0],bs.inputs["Base Color"])
for o in [obj("City gate lintel"),obj("City gate jambs")]:
    for p in o.data.polygons:
        if p.material_index==3:p.use_smooth=True
for name in ("City gate lintel","City gate jambs","City gate core","Entrance wall | coursed rubble"):
    for v in obj(name).data.vertices:v.co.z=11.72+(v.co.z-11.72)*1.085

def bend_banks(o):
    count=len(o.data.vertices)
    pts=np.empty(3*count,np.float32)
    o.data.vertices.foreach_get("co",pts)
    p=pts.reshape((-1,3));x,y,z=p.T
    origin=np.interp(np.minimum(-29,y),[-72,-54,-51.39,-46.65,-41.5,-35,-29],[100,76,68.18,62.52,60.72,60.65,60.52])+2+np.maximum(0,y+33)*.93
    idx=np.clip((x-origin)/3.35,0,14)
    x+=np.minimum(1,idx)*(.8*np.sin(y*.098+idx*.31)+1.14*np.sin(y*.046-idx*.48))+.35*np.sin(y*.13)
    x-=.53*np.maximum(0,y+34)
    z-=np.clip((y+38)/44,0,1)**1.46*(5.5+1.1*np.sin(idx*.51))
    z+=.12*np.sin(y*.105+idx*.49)
    o.data.vertices.foreach_set("co",pts);o.data.update()
for o in list(bpy.data.objects):
    if o.name.startswith(("Agricultural platform","Agricultural terrace","Anden retaining","Andenes |","Turf tufts")):bend_banks(o)
for v in obj("Urban passage walls").data.vertices:
    x,y,z=v.co
    if z>road(y)+.4:
        v.co.z+=(.10*math.sin(y*1.72)+.073*math.sin(y*3.1+x))*(1 if x>60 else .7)
rooms=obj("Urban courts | windowless gables and stone stairs").data
for v in rooms.vertices:
    x,y,z=v.co
    if 58.5<x<69 and -18.7<y<2.5:
        descent=min(1,max(0,(y+18.7)/14))*1.75
        v.co.z-=descent
for slot in range(3):
    m=rooms.materials[slot].copy();m.name="Quarried granite | inner court "+str(slot)
    nt=m.node_tree;bs=nt.nodes["Principled BSDF"]
    tint=nt.nodes.new("ShaderNodeMixRGB");tint.blend_type="MULTIPLY";tint.inputs[0].default_value=1
    tint.inputs[2].default_value=(.52,.56,.55,1)
    nt.links.new(bs.inputs["Base Color"].links[0].from_socket,tint.inputs[1])
    nt.links.new(tint.outputs[0],bs.inputs["Base Color"]);rooms.materials[slot]=m
vs=[]
for j in range(112):
    y=-56+j/111*33
    for k in range(5):
        x=edge(y)+1.45+k*1.37
        z=road(y)-.09-.052*k-.048*k*k
        vs.append((x,y,z))
mesh("Terrace head | natural soil",vs,grid(5,112),[grass])

# Scattered dry tufts follow patches of exposed turf instead of lining every bank.
vs=[];faces=[];ids=[]
for i in range(8):
    bank=obj("Agricultural platform %02d"%(i+1))
    for j in range(170):
        y=rng.uniform(-57,2)
        x=edge(min(-29,y))+3.4+i*3.33+max(0,y+33)*.93+rng.uniform(-1.65,1.65)
        hit,pos,_,_=bank.ray_cast(Vector((x,y,90)),Vector((0,0,-1)))
        if not hit:continue
        for _ in range(rng.randrange(12,31)):
            dx,dy=rng.gauss(0,.22),rng.gauss(0,.19)
            a,b=x+dx,y+dy;z=pos.z-.032
            h=rng.uniform(.07,.23);w=rng.uniform(.006,.015);k=len(vs)
            vs.extend([(a-w,b,z),(a+w,b,z),(a+w*.5+.04,b+.016,z+h*.64),(a+.085,b+.03,z+h)])
            faces.extend([(k,k+1,k+2),(k,k+2,k+3)]);ids.extend([rng.choice((0,0,2)),rng.choice((0,1,1))])
mesh("Mountain grass | bunches and dry tips",vs,faces,[mat("Grass | green"),mat("Grass | straw tips"),mat("Grass | shadow")],ids)
bank=obj("Agricultural platform 01")
model=next((LIB/"namaqualand_stones_01").glob("*.gltf"))
outdoor.plant(str(model),[(64.1,-38,.79),(64.6,-38.4,.49),(64.2,-38.8,.39),(65.4,-44,.61),(66,-43.9,.35)],.8,bank,sink=.13,seed=14,name="Granitic outcrop")
bs=grass.node_tree.nodes["Principled BSDF"]
nt=grass.node_tree;tc=nt.nodes.new("ShaderNodeTexCoord")
noise=nt.nodes.new("ShaderNodeTexNoise");noise.inputs["Scale"].default_value=1.65;noise.inputs["Detail"].default_value=5
nt.links.new(tc.outputs["Object"],noise.inputs["Vector"])
ramp=nt.nodes.new("ShaderNodeValToRGB");ramp.color_ramp.elements[0].position=.27;ramp.color_ramp.elements[0].color=(.26,.46,.23,1)
ramp.color_ramp.elements[1].position=.72;ramp.color_ramp.elements[1].color=(.89,1,.62,1)
nt.links.new(noise.outputs["Fac"],ramp.inputs[0])
product=nt.nodes.new("ShaderNodeMixRGB");product.blend_type="MULTIPLY";product.inputs[0].default_value=.82
nt.links.new(bs.inputs["Base Color"].links[0].from_socket,product.inputs[1]);nt.links.new(ramp.outputs[0],product.inputs[2])
nt.links.new(product.outputs[0],bs.inputs["Base Color"])
outdoor.sky(str(next((LIB/"evening_road_01_puresky").glob("*.hdr"))),
   sun_azimuth=232,preset="dusk",sun_elevation=24,sun_strength=4.1,sun_color=(1,.84,.64),strength=.56,
   sun_angle=2.0,disc=0,exposure=.02)
