"""Register the wide valley view to the angular coordinates of the dusk photograph."""
from pathlib import Path
from PIL import Image
import numpy as np

root=Path(__file__).resolve().parents[1]
pano=np.asarray(Image.open(root/"photos/valley-1.jpg").convert("RGB"),dtype=np.float32)
far_slope=np.asarray(Image.open(root/"photos/valley-2.jpg").convert("RGB"),dtype=np.float32)
late=np.asarray(Image.open(root/"photos/ridge-at-dusk.jpg").convert("RGB"),dtype=np.float32)
H,W=1880,3150
v,u=np.mgrid[0:H,0:W].astype(np.float32)
u=u/(W-1)*2310-700;v=v/(H-1)*1520-470
ang=np.arctan((u-313)/615)
px=436+430*ang
py=159+(v-320)*430/615*np.cos(ang)
def sample(image,x,y,w,h):
    iy=np.clip((y/h*(image.shape[0]-1)).astype(int),0,image.shape[0]-1)
    ix=np.clip((x/w*(image.shape[1]-1)).astype(int),0,image.shape[1]-1)
    return image[iy,ix]
def upper(points,x):
    return np.interp(x,*zip(*points))
# Replace only foreground hidden in the reference; the adjoining exposed slopes give
# the far layer enough overlap for the change of viewpoint on the 33 m walk.
near=upper([(0,28),(112,72),(154,111),(222,122),(259,120),(285,125),(311,93),
 (328,110),(345,107),(370,111),(390,92),(409,58),(427,44),(441,60),(459,78),
 (483,102),(506,121),(529,146),(555,165),(585,196),(615,218),(640,242),(685,256),(850,265)],px)
near-=np.interp(px,[0,110,180,255,340,425,535,650],[22,19,33,28,19,13,6,0])
blend=np.clip((py-near)/6,0,1)*np.clip((654-px)/23,0,1)
def mirror(z,lo,hi):
    return lo+(hi-lo)-np.abs((z-lo)%(2*(hi-lo))-(hi-lo))
sky_y=np.where(py<5,mirror(py,-1,23),py)
source=sample(pano,px,sky_y,850,329)
sample_x=mirror(px*.79+53,551,637)
sample_y=mirror(py*.82+19,97,179)
wood=sample(pano,sample_x,sample_y,850,329)
source=(source*(1-blend[...,None])+wood*blend[...,None])
roof=np.clip((px-642)/14,0,1)*np.clip((py-np.interp(px,[642,680,712,754,780,800,850],[245,242,243,238,211,177,178]))/7,0,1)
east=sample(pano,mirror(px-48+.13*py,660,754),mirror(py-52,125,205),850,329)
source=source*(1-roof[...,None])+east*roof[...,None]
# Continue the descending flank with forest from a second valley photograph.
# A broad feather below the eastern buttress keeps the slope continuous.
fx=mirror(147+.54*(px-715)+.21*(py-218),93,211)
fy=mirror(114+.61*(py-218)-.09*(px-715),91,159)
forest=sample(far_slope,fx,fy,850,291)*np.array([.88,.95,.92])
low=np.clip((py-(203+12*np.sin(px*.022)))/35,0,1)*np.clip((px-605)/50,0,1)
source=source*(1-low[...,None])+forest*low[...,None]
mist=np.clip((1-py)/90,0,.92)[...,None]
source=source*(1-mist)+np.array([184,183,191])*mist
source=source*np.array([1.07,.998,.905])+np.array([4,4,5])
skyline=upper([(0,220),(42,228),(85,222),(114,211),(151,193),(190,197),(236,191),
 (260,190),(357,186),(460,184),(505,181),(552,195),(635,195)],u)
peak=upper([(0,438),(46,362),(90,300),(138,268),(174,305),(210,274),(247,204),
 (281,164),(300,157),(320,164),(345,201),(370,226),(404,245),(470,257),(530,277),
 (579,338),(635,362)],u)
original=sample(late,u,v,635,620)
weight=np.clip((peak-v-5)/22,0,1)*np.clip((u-5)/84,0,1)*np.clip((630-u)/85,0,1)*np.clip((v-1)/29,0,1)
out=original*weight[...,None]+source*(1-weight[...,None])
Image.fromarray(np.clip(out,0,255).astype("uint8")).save(root/"build/valley-color.jpg",quality=94)
