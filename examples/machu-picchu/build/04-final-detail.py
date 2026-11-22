"""Finish the sky cutline, ashlar opening and the roofless granite city."""
import json
import math
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector

root=Path(__file__).resolve().parents[1]
scene=bpy.context.scene
def image_material(name,path):
    m=bpy.data.materials[name]
    tex=next(n for n in m.node_tree.nodes if n.type=="TEX_IMAGE")
    tex.image=bpy.data.images.load(str(path),check_existing=True)
    tex.extension="EXTEND"
    return m
far=image_material("Cordillera | distant light",root/"build/cordillera-rock.jpg")
city=image_material("City terraces | survey colour",root/"build/city-albedo.jpg")
outer=bpy.data.objects["Cordillera and evening sky"]
co,si=math.cos(math.radians(7.1)),math.sin(math.radians(7.1))
def place(u,v,d):
    side=(u-313)*d/615
    return (92-si*d+co*side,-104+co*d+si*side,30.5+(320-v)*d/615)
def mesh(name,vs,faces,materials,uv=None):
    m=bpy.data.meshes.new(name);m.from_pydata(vs,[],faces);m.update()
    for material in materials:m.materials.append(material)
    if uv is not None:
        layer=m.uv_layers.new(name="Survey UV")
        ix=np.empty(len(m.loops),np.int32);m.loops.foreach_get("vertex_index",ix)
        layer.data.foreach_set("uv",np.array(uv,np.float32)[ix].ravel())
    o=bpy.data.objects.new(name,m);scene.collection.objects.link(o)
    return o
crest=np.loadtxt(root/"build/cordillera-horizon.csv",delimiter=",")
vs=[];uv=[];nx,ny=1350,32
for j in range(ny):
    t=j/(ny-1)
    for i in range(nx):
        s=i/(nx-1);px=-80+s*1010
        py=np.interp(px,crest[:,0],crest[:,1])+338*t
        angle=(px-436)/430
        u=313+615*math.tan(angle)
        v=320+(py-159)*615/430/math.cos(angle)
        vs.append(place(u,v,5200+250*math.cos(px*.012)))
        uv.append((s,1-t))
mesh("Cordillera | forest below the skyline",vs,
     [(k,k+1,k+1+nx,k+nx) for k in range((ny-1)*nx) if k%nx<nx-1],[far],uv)
bpy.data.objects.remove(outer,do_unlink=True)
# Only the photographed, spherical environment is visible above the ridge.
world=scene.world.node_tree
env=next(n for n in world.nodes if n.type=="TEX_ENVIRONMENT")
output=next(n for n in world.nodes if n.type=="OUTPUT_WORLD")
old=output.inputs["Surface"].links[0].from_socket
hue=world.nodes.new("ShaderNodeHueSaturation");hue.inputs["Saturation"].default_value=.77
world.links.new(env.outputs["Color"],hue.inputs["Color"])
sky=world.nodes.new("ShaderNodeBackground");sky.inputs["Strength"].default_value=.73
world.links.new(hue.outputs["Color"],sky.inputs["Color"])
mix=world.nodes.new("ShaderNodeMixShader");light=world.nodes.new("ShaderNodeLightPath")
world.links.new(light.outputs["Is Camera Ray"],mix.inputs[0]);world.links.new(old,mix.inputs[1])
world.links.new(sky.outputs[0],mix.inputs[2]);world.links.new(mix.outputs[0],output.inputs["Surface"])
# Sample several pixels inside the recorded silhouette to exclude its sky fringe.
peak=bpy.data.objects["Huayna Picchu"].data
layer=peak.uv_layers.active
uv=np.empty(len(peak.loops)*2,np.float32);layer.data.foreach_get("uv",uv);uv=uv.reshape(-1,2)
u=uv[:,0]*635;v=(1-uv[:,1])*620
tops=np.array([(0,443),(48,364),(88,306),(138,271),(174,309),(208,279),(244,209),
 (268,181),(288,164),(299,161),(308,162),(320,168),(345,205),(369,227),(404,249),(453,259),
 (479,263),(505,272),(527,281),(543,300),(561,322),(579,343),(604,356),(635,369)])
dist=np.maximum(0,v-np.interp(u,tops[:,0],tops[:,1]))
uv[:,1]-=.013*np.exp(-dist/14)
layer.data.foreach_set("uv",uv.ravel())

# Dimensions are measured in the plane of the face. The McKay Savage photograph
# shows about 380 px clear below the lintel and 415 px at the foot. The back reveal
# is splayed, so its edge does not mask the batter of the dressed front stones.
for name in ("City gate jambs","City gate lintel","City gate core","Entrance wall | coursed rubble"):
    data=bpy.data.objects[name].data
    for vert in data.vertices:
        x,y,z=vert.co;u=x-60.48;h=(z-11.72)/1.085
        t=np.clip(h/3.24,0,1)
        # Lower clearance 1.94 m, upper clearance 1.59 m after rectification.
        near=np.clip((2.45-abs(u))/1.28,0,1)
        reveal=np.clip((y-(-27.51-.45+h*.025))/.99,0,1)
        offset=(.115*(1-t)-.023*t+.22*reveal)*near
        vert.co.x+=math.copysign(offset,u)
    data.update()
    if name in ("City gate jambs","City gate lintel"):
        for face in data.polygons:face.use_smooth=False
m=bpy.data.materials["Gate face study / granite microrelief"]
nt=m.node_tree;bs=nt.nodes["Principled BSDF"]
if bs.inputs["Roughness"].is_linked:nt.links.remove(bs.inputs["Roughness"].links[0])
bs.inputs["Roughness"].default_value=.59
for node in nt.nodes:
    if node.type=="BUMP":node.inputs["Strength"].default_value*=.28

# Reinstate open gables on their mapped footprints where the relief covers
# building foundations. The short ends slope up to a ridge; there is no roof.
surface=bpy.data.objects["Upper city | plaza, gables and quarry"]
stone=bpy.data.materials["Fieldstone courses"].copy();stone.name="Grey granite | urban gable ends"
nt=stone.node_tree;b=nt.nodes["Principled BSDF"]
h=nt.nodes.new("ShaderNodeHueSaturation");h.inputs["Saturation"].default_value=.11
h.inputs["Value"].default_value=.82
nt.links.new(b.inputs["Base Color"].links[0].from_socket,h.inputs["Color"])
nt.links.new(h.outputs[0],b.inputs["Base Color"])
bpy.data.objects["Urban ensemble"].data.materials[0]=stone
vs=[];faces=[]
def panel(a,b,bottom,ha,hb):
    vec=b-a;normal=np.array([-vec[1],vec[0]])*.29/np.linalg.norm(vec)
    k=len(vs)
    for p,h in ((a,ha),(b,hb)):
        for sign in (-1,1):
            vs.extend([(*list(p+normal*sign),bottom),(*list(p+normal*sign),h)])
    for f in ((0,1,5,4),(2,6,7,3),(1,3,7,5),(0,2,3,1),(4,5,7,6)):
        faces.append(tuple(k+n for n in f))
for f in json.loads((root/"place/features.json").read_text()):
    if f["kind"]!="building" or len(f["points"])!=4 or not f["closed"]:continue
    ps=np.array(f["points"],float);x,y=ps.mean(axis=0)
    if not (-40<x<149 and 45<y<252):continue
    lengths=np.linalg.norm(np.roll(ps,-1,axis=0)-ps,axis=1)
    if not (3.2<min(lengths)<9 and 5<max(lengths)<20):continue
    hit,pt,_,_=surface.ray_cast(Vector((x,y,130)),Vector((0,0,-1)))
    if not hit:continue
    floor=pt.z-1.04
    for i in range(4):
        a,b=ps[i],ps[(i+1)%4];mid=(a+b)*.5
        top=floor+1.9
        if lengths[i]<max(lengths)*.82:
            panel(a,mid,floor-1.4,top,top+min(1.95,lengths[i]*.39))
            panel(mid,b,floor-1.4,top+min(1.95,lengths[i]*.39),top)
        elif i==int(np.argmax(lengths)):
            p=a+(b-a)*.47;q=p+(b-a)*(1.1/lengths[i])
            panel(a,p,floor-1.4,top,top);panel(q,b,floor-1.4,top,top)
        else:panel(a,b,floor-1.4,top,top)
if vs:mesh("Urban ridge | roofless granite gables",vs,faces,[stone])
