"""Carve the gate elevation, set individual paving stones, retain the terrace soil."""
import math
from pathlib import Path
import random

import bpy
import numpy as np
from modeling import outdoor

ROOT=Path(__file__).resolve().parents[1]
import home
LIB = home.library_root() / "polyhaven"
for _asset in ("lichen_rock",):
    outdoor.library("polyhaven:" + _asset)   # downloads what this machine does not have yet
randomizer=random.Random(6842)
R=randomizer
for o in list(bpy.data.objects):
    if o.name.startswith("Blockout |"):
        bpy.data.objects.remove(o,do_unlink=True)
granite=bpy.data.materials["Dressed granite"]
lichen=outdoor.material(str(LIB/"lichen_rock"),metres=2.45,tint=(.85,.87,.75),relief=.55,name="Lichen crust / granite")
field=bpy.data.materials["Fieldstone courses"]
grass=bpy.data.materials["Andean turf"]
soil=bpy.data.materials["Weathered trail bed"]
def swatch(name,rgb):
    m=bpy.data.materials.new(name);m.diffuse_color=(*rgb,1)
    m.use_nodes=True
    bs=m.node_tree.nodes["Principled BSDF"]
    bs.inputs["Base Color"].default_value=(*rgb,1);bs.inputs["Roughness"].default_value=.86
    return m
slate=granite.copy();slate.name="Granite | cool inclusion"
for node in slate.node_tree.nodes:
    if node.bl_idname=="ShaderNodeMix" and node.blend_type=="MULTIPLY":
        node.inputs[7].default_value=(.43,.50,.50,1)
hero=granite.copy();hero.name="Gate face study / granite microrelief"
n=hero.node_tree.nodes
tex=n.new("ShaderNodeTexImage")
tex.image=bpy.data.images.load(str(ROOT/"photos/city-gate.jpg"),check_existing=True)
uv=n.new("ShaderNodeUVMap");uv.uv_map="Stone face"
hero.node_tree.links.new(uv.outputs[0],tex.inputs[0])
mix=n.new("ShaderNodeMixRGB");mix.blend_type="MIX";mix.inputs[0].default_value=.13
bs=n["Principled BSDF"]
old=bs.inputs["Base Color"].links[0].from_socket
hero.node_tree.links.new(tex.outputs["Color"],mix.inputs[1])
hero.node_tree.links.new(old,mix.inputs[2]);hero.node_tree.links.new(mix.outputs[0],bs.inputs["Base Color"])
grasses=[swatch(n,c) for n,c in [("Grass | green",(.115,.182,.048)),("Grass | straw tips",(.27,.235,.095)),("Grass | shadow",(.049,.09,.023))]]
MATS=[granite,slate,lichen,hero,field,soil,grass]+grasses


class Surface:
    def __init__(self):
        self.verts=[];self.faces=[];self.mats=[];self.uv=[]
    def face(self,verts,mat,uv=None):
        i=len(self.verts);self.verts.extend(verts)
        self.uv.extend(uv if uv else [(0,0)]*len(verts))
        self.faces.append(tuple(range(i,i+len(verts))));self.mats.append(mat)
    def finish(self,name):
        data=bpy.data.meshes.new(name);data.from_pydata(self.verts,[],self.faces);data.update()
        for m in MATS:data.materials.append(m)
        data.polygons.foreach_set("material_index",self.mats)
        if 3 in self.mats:
            layer=data.uv_layers.new(name="Stone face")
            layer.data.foreach_set("uv",np.asarray(self.uv,np.float32).ravel())
        obj=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(obj)
        return obj


def area(poly):
    return sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1]))/2
def between(a,b,t):
    return (a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
def clip(poly,nx,ny,limit):
    out=[]
    for a,b in zip(poly,poly[1:]+poly[:1]):
        fa=nx*a[0]+ny*a[1]-limit;fb=nx*b[0]+ny*b[1]-limit
        if fa<=0:out.append(a)
        if (fa<0<fb) or (fb<0<fa):out.append(between(a,b,fa/(fa-fb)))
    return out


def stone(batch,poly,frame,depth=.54,mat=0,photo=False,bevel=.031):
    if len(poly)<3 or abs(area(poly))<.0008:return
    if area(poly)<0:poly=poly[::-1]
    p=[]
    for i,a in enumerate(poly):
        prev,nxt=poly[i-1],poly[(i+1)%len(poly)]
        if math.dist(prev,a)<.001 or math.dist(a,nxt)<.001:continue
        # Vary the length of individual chipped arrises without rounding entire blocks.
        p.extend([between(a,prev,min(.18,R.uniform(.35,1.4)*bevel/max(.001,math.dist(a,prev)))),
                  between(a,nxt,min(.18,R.uniform(.35,1.4)*bevel/max(.001,math.dist(a,nxt))))])
    if len(p)<3:return
    mid=tuple(sum(q[k] for q in p)/len(p) for k in (0,1))
    size=max(.03,math.sqrt(abs(area(poly))))
    outer=[between(q,mid,min(.12,(.003 if photo else .008)/size)) for q in p]
    inner=[between(q,mid,min(.24,bevel*1.6/size)) for q in p]
    lip=[frame(*q,.023+R.uniform(-.004,.004)) for q in outer]
    face=[frame(*q,0 if photo else R.uniform(-.003,.003)) for q in inner]
    back=[frame(*q,depth) for q in outer]
    def coords(q):
        x,z=q
        y=1255-z*242
        return ((510-(1255-y)*.027+x*242)/960,1-y/1440)
    batch.face(face,3 if photo else mat,[coords(q) for q in inner] if photo else None)
    for j,a in enumerate(inner):
        k=(j+1)%len(p)
        batch.face((face[j],lip[j],lip[k],face[k]),mat)
        batch.face((lip[j],back[j],back[k],lip[k]),mat)
    batch.face(back[::-1],mat)


def cells(polygon,stepx,stepy):
    x0,x1=min(p[0] for p in polygon),max(p[0] for p in polygon)
    y0,y1=min(p[1] for p in polygon),max(p[1] for p in polygon)
    if x1-x0<.08 or y1-y0<.06:return
    nx=max(1,round((x1-x0)/stepx));ny=max(1,round((y1-y0)/stepy))
    sx,sy=(x1-x0)/nx,(y1-y0)/ny
    seeds={(i,j):(x0+(i+.5+R.uniform(-.34,.34))*sx,y0+(j+.5+R.uniform(-.34,.34))*sy)
           for i in range(nx) for j in range(ny)}
    for (i,j),(x,y) in seeds.items():
        poly=polygon[:]
        for ii in range(max(0,i-2),min(nx,i+3)):
            for jj in range(max(0,j-2),min(ny,j+3)):
                if (ii,jj)==(i,j):continue
                xx,yy=seeds[ii,jj]
                ax=(xx-x)/sx**2;ay=(yy-y)/sy**2
                if poly:poly=clip(poly,ax,ay,ax*(xx+x)/2+ay*(yy+y)/2)
        if len(poly)>=3:yield poly


def rect(x0,y0,x1,y1):
    return [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
def variant():
    return R.choices([0,1,2],weights=[.63,.275,.095])[0]
def scattered(batch,poly,step,frame,depth=.45,bevel=.027):
    for cell in cells(poly,*step):stone(batch,cell,frame,depth,variant(),bevel=bevel)


gx,gy,gz=60.48,-27.51,11.72
def facade(u,z,d):
    return (gx+u,gy-.45+z*.025+d,gz+z)
def from_photo(pt):
    x,y=pt
    return ((x-(510-(1255-y)*.027))/242,(1255-y)/242)

# Each outline follows a visible joint of the McKay Savage front elevation. The single
# lintel and the uneven large blocks of each jamb are independent volumes.
import json
joints=json.loads((ROOT/"build/gate-joints.json").read_text())
left_tiles,right_tiles,lintel=[joints[k] for k in ("left_tiles","right_tiles","lintel")]
jambs=Surface()
for tile in left_tiles+right_tiles:
    stone(jambs,[from_photo(pt) for pt in tile],facade,1.12,1,True,.010)
jambs.finish("City gate jambs")
head=Surface()
stone(head,[from_photo(p) for p in lintel],facade,1.18,1,True,.024)
head.finish("City gate lintel")

# Build the backing and the surrounding field to the photo silhouette. Exposed edges
# and the far face are faced with stone, including the reverse of the entrance.
shell=Surface()
def fill_wall(x0,z0,x1,z1,depth,front=.10):
    a,b,c,d=[facade(x,z,front) for x,z in rect(x0,z0,x1,z1)]
    aa,bb,cc,dd=[facade(x,z,depth) for x,z in rect(x0,z0,x1,z1)]
    for f in [(a,b,c,d),(aa,dd,cc,bb),(a,aa,bb,b),(b,bb,cc,c),(c,cc,dd,d),(d,dd,aa,a)]:shell.face(f,1)
fill_wall(-2.94,-.2,-.90,3.28,1.06)
fill_wall(.93,-.2,2.78,3.28,1.06)
fill_wall(-2.94,3.27,2.78,4.86,1.06)
decor=Surface()
for poly in json.loads((ROOT/"build/gate-infill.json").read_text()):
    stone(decor,poly,facade,.82,variant(),bevel=.016)
def reverse(u,z,d):return facade(u,z,1.14-d)
for poly in (rect(-2.96,-.18,-.83,3.26),rect(.83,-.18,2.8,3.26),rect(-2.96,3.26,2.8,4.92)):
    scattered(decor,poly,(.69,.43),reverse,.34)
for x,sgn in [(-2.97,1),(2.8,-1)]:
    scattered(decor,rect(0,-.2,1.16,4.94),(.6,.45),lambda u,z,d,x=x,sgn=sgn:facade(x+sgn*d,z,u),.36)
shell.finish("City gate core")
decor.finish("Entrance wall | coursed rubble")


def road_z(y):
    return float(np.interp(y,[-100,-53,-51.39,-46.65,-41.5,-36.8,-33.1,-29,-27.51,-24,-21,-13,8,40],
      [22,16.1,15.8,14.65,13.15,11.91,11.55,11.70,11.72,11.5,11.2,10,5.2,-1.5]))
def edge(y):
    return float(np.interp(y,[-72,-54,-51.39,-46.65,-41.5,-35,-27.51,-13.6,7.7,37],
      [100,76,68.18,62.52,60.72,60.65,60.48,60.4,57.35,54]))


def linear_wall(batch,a,b,lo,hi,thick=.6,scale=(.63,.38)):
    ax,ay=a;bx,by=b
    length=math.dist(a,b)
    if length<.1:return
    ux,uy=(bx-ax)/length,(by-ay)/length
    def f(u,v,d):
        t=u/length
        return (ax+ux*u-uy*d,ay+uy*u+ux*d,lo[0]*(1-t)+lo[1]*t+v)
    h0,h1=hi[0]-lo[0],hi[1]-lo[1]
    poly=[(0,0),(length,0),(length,h1),(0,h0)]
    scattered(batch,poly,scale,f,thick)
    # The backs and cap retain the soil and remain visible from above.
    a,b,c,d=[f(u,v,thick*.68) for u,v in poly]
    batch.face([a,d,c,b],4)
    batch.face([f(0,h0,.02),f(length,h1,.02),c,d],0)


side=Surface()
for a,b,lo,hi,w in [
    ((gx+2.8,gy+.4),(74.81,-14.16),(9.6,6.7),(13.90,10.2),.93),
    ((74.81,-14.16),(84.33,-5.39),(6.7,3.2),(10.2,7.4),.90)]:
    linear_wall(side,a,b,lo,hi,w)
# Lay stones across the bends of the west approach in a single run. Their dressed
# faces point into the passage. A continuous granite bed closes the deep joints.
west=[(60.1,-49.6,13.43,16.10),(gx-4.70,gy-15,12.88,16.74),
      (gx-3.63,gy-9.9,12.47,16.65),(gx-2.91,gy+.46,gz-.5,gz+5.32)]
wp=np.asarray(west)
ds=np.r_[0,np.cumsum(np.linalg.norm(np.diff(wp[:,:2],axis=0),axis=1))]
normals=np.diff(wp[:,:2],axis=0)/np.diff(ds)[:,None]
normals=np.c_[-normals[:,1],normals[:,0]]
corner=np.vstack((normals[0],(normals[:-1]+normals[1:])/2,normals[-1]))
def west_frame(u,v,d):
    i=min(len(ds)-2,max(0,int(np.searchsorted(ds,u,side="right")-1)))
    t=(u-ds[i])/(ds[i+1]-ds[i])
    a=wp[i]*(1-t)+wp[i+1]*t
    n=corner[i]*(1-t)+corner[i+1]*t
    return (a[0]+n[0]*d,a[1]+n[1]*d,a[2]+v*(a[3]-a[2]))
for poly in cells(rect(0,0,ds[-1],1),.68,.115):
    stone(side,poly,west_frame,.65,variant(),bevel=.012)
for a,b in zip(ds,ds[1:]):
    for v in (0,1):
        side.face([west_frame(a,v,d) for d in (.02,.84)]+[west_frame(b,v,d) for d in (.84,.02)],0)
    for d in (.10,.80):
        side.face([west_frame(u,v,d) for u,v in [(a,0),(b,0),(b,1),(a,1)]],1)
side.finish("Approach walls | battered granite")

passage=Surface()
for sgn in (-1,1):
    pts=[(gx+sgn*1.55,gy+.76),(60.4+sgn*1.57,-21),(60.4+sgn*1.67,-13.6),(58.5+sgn*1.62,.1),(57.35+sgn*1.6,7.7)]
    for i,(a,b) in enumerate(zip(pts,pts[1:])):
        if sgn==1:a,b=b,a
        za,zb=road_z(a[1]),road_z(b[1])
        linear_wall(passage,a,b,(za-.32,zb-.32),(za+(1.04 if sgn<0 else 1.29),zb+(1.05 if sgn<0 else 1.28)),.65,(.61,.33))
passage.finish("Urban passage walls")

paving=Surface()
for y0,y1 in zip([-61,-55,-51,-47,-43,-39,-35,-31,-27.5,-23,-19,-15,-11,-7,-3,1,5,9,13,17],
                 [-55,-51,-47,-43,-39,-35,-31,-27.5,-23,-19,-15,-11,-7,-3,1,5,9,13,17,20]):
    for poly in cells(rect(-1.56,y0,1.56,y1),.76,.52):
        yf=sum(p[1] for p in poly)/len(poly)
        z=road_z(yf)+.07+R.uniform(-.007,.019)
        def surface(x,y,d):
            return (edge(y)+x,y,z+.32*(road_z(y)-road_z(yf))-.023*abs(x)-d)
        stone(paving,poly,surface,.24,R.choices([0,1,2],[.68,.29,.03])[0],bevel=.024)
paving.finish("Urban paving")

terr=Surface()
blade=Surface()
def bank_x(i,y):
    t=(y+82)/88
    return edge(min(-29,y))+2+i*(3.1+.34*math.sin(t*2.5))+max(0,y+33)*.93+1.2*math.sin(2.4*t+i*.14)
def bank_z(i,y):
    return road_z(min(y,-26))-.18-i*1.19+.11*math.sin((y+82)/88*2.3+i*.3)
for i in range(11):
    for y0 in range(-66,5,6):
        def plane(u,z,d):
            y=y0+u
            return (bank_x(i+1,y)+.095-d+.105*(1-z),y,bank_z(i,y)-1.24+z)
        scattered(terr,rect(0,0,6,1.25),(.54+.055*i,.32),plane,.32,.035)
    for _ in range(920 if i<5 else 350):
        y=R.uniform(-64,5)
        x=bank_x(i+1,y)-abs(R.gauss(.19,.16))
        z=bank_z(i,y)-.05
        for q in range(R.randint(3,7)):
            dx,dy=R.uniform(-.09,.09),R.uniform(-.09,.09)
            h,w=R.uniform(.045,.18),R.uniform(.003,.016)
            blade.face([(x+dx-w,y+dy,z),(x+dx+w,y+dy,z),(x+dx+dx*.45,y+dy+dy*.55,z+h)],R.choice([7,7,8,9]))
terr.finish("Andenes | individual retaining stones")
blade.finish("Turf tufts at terrace lips")

# The eastern kancha, the lower room across the path and the platform to its west.
rooms=Surface()
def court(corners,floor,walls,entry,gable):
    for i,(a,b) in enumerate(zip(corners,corners[1:]+corners[:1])):
        length=math.dist(a,b)
        def rim(t):
            return floor+walls[i]+max(0,1-abs(t-.50)*2)*gable.get(i,0)+.11*math.sin(t*11+i)
        half=.60/length
        stops=[0,.25,.50,.75,1] if i in gable else [0,.32,.67,1]
        if i==entry:stops=sorted(set(stops+[.50-half,.50+half]))
        for t0,t1 in zip(stops,stops[1:]):
            left,right=between(a,b,t0),between(a,b,t1)
            opening=i==entry and .50-half<=(t0+t1)/2<=.50+half
            base=floor+1.92 if opening else floor-.85
            if min(rim(t0),rim(t1))<base+.10:continue
            linear_wall(rooms,left,right,(base,base),(rim(t0),rim(t1)),.59,(.56,.32))
        if i==entry:
            ux,uy=(b[0]-a[0])/length,(b[1]-a[1])/length
            mx,my=between(a,b,.5)
            for j in range(4):
                def step(u,v,d):
                    return (mx+ux*u+uy*v,my+uy*u-ux*v,floor+.04-j*.16-d)
                stone(rooms,rect(-.86,j*.34,.87,j*.34+.36),step,.25,0,bevel=.027)
court([(61.71,-18.70),(68.47,-17.78),(65.72,2.39),(58.96,1.47)],10.51,[2.23,1.6,2.36,1.72],0,{0:.74,2:1.10})
court([(59.11,7.53),(64.16,8.53),(61.05,24.28),(56,23.29)],6.72,[2.47,2.55,2.22,2.35],0,{0:1.27})
court([(42.75,-21.42),(51.26,-19.67),(50.15,-14.32),(41.66,-16.05)],10.25,[1.70,2.39,1.7,2.3],1,{3:1.3})
rooms.finish("Urban courts | windowless gables and stone stairs")
