"""Prepare mountain-only projection and stone/grass albedo for the city relief."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

root=Path(__file__).resolve().parents[1]
out=root/"build"
photo=np.asarray(Image.open(root/"photos/valley-1.jpg").convert("RGB"))
# Survey coordinates are pixels in the 850 x 329 reference. A common X axis is
# used for the mountains and the camera, independently of the spherical sky.
horizon=[(0,29),(40,51),(70,64),(96,72),(125,78),(154,78),(184,81),
 (207,83),(238,84),(258,86),(280,99),(300,92),(325,91),(340,98),(361,99),
 (392,85),(423,79),(459,82),(479,82),(502,76),(522,74),(550,74),(567,73),
 (583,82),(602,79),(625,86),(641,72),(657,70),(675,66),(692,68),(709,72),
 (722,65),(731,76),(747,76),(762,72),(780,68),(797,59),(806,66),(826,63),(850,62)]
# Trace the boundary at half reference-pixel intervals. The ridge behind the near
# summit is hidden in the panorama; continue that short section from a neighbouring
# stretch of the same ridge, with matching endpoint heights.
mini=np.array(Image.fromarray(photo).resize((1700,658),Image.Resampling.LANCZOS),float)
gray=mini@np.array([.27,.59,.14])
X=np.arange(1700);px0=X/2
expected=np.interp(px0,*zip(*horizon))*2
cost=np.abs(np.arange(230)[:,None]-expected)*.78
edge_score=gray[:230]-gray[3:233]-cost
path=np.zeros_like(edge_score,dtype=np.int16)
dp=edge_score[:,0]
for j in range(1,1700):
    pad=np.pad(dp,(3,3),constant_values=-1e6)
    opts=np.array([pad[k:k+230]-abs(k-3)*4 for k in range(7)])
    path[:,j]=np.argmax(opts,axis=0)-3
    dp=edge_score[:,j]+opts.max(axis=0)
iy=int(dp.argmax());trace=np.zeros(1700)
for j in range(1699,-1,-1):
    trace[j]=(iy+1.2)/2;iy+=int(path[iy,j])
amb=(px0>=382)&(px0<=505);q=np.linspace(0,1,amb.sum())
detail=np.interp(532+103*q,px0,trace)
trace[amb]=detail+np.interp(q,[0,1],[trace[763]-detail[0],trace[1011]-detail[-1]])
np.savetxt(out/"cordillera-horizon.csv",np.c_[px0,trace],fmt="%.3f",delimiter=",")
P,T=np.meshgrid(np.linspace(-80,930,3430,dtype=np.float32),np.linspace(0,1,990,dtype=np.float32))
def triangular(x,lo,hi):
    span=hi-lo
    return hi-abs((x-lo)%(2*span)-span)
def sample(px,py):
    x=np.clip(px/850*(photo.shape[1]-1),0,photo.shape[1]-1).astype(int)
    y=np.clip(py/329*(photo.shape[0]-1),0,photo.shape[0]-1).astype(int)
    return photo[y,x].astype(np.float32)
def ramp(x):
    x=np.clip(x,0,1)
    return (x*x*(3-2*x))[...,None]
# Hide the near summit and the archaeological site in the source panorama. The
# modelled massif and city occupy this space as independent geometry.
px=np.clip(P,0,849)
reference_top=np.interp(px,px0,trace)
row=reference_top+4+T*338
src=sample(px,row)
forest_x=triangular(623+.72*(P-615)+.11*(row-185),546,754)
forest_y=triangular(171+.60*(row-180)+.08*(P-610),151,207)
forest=sample(forest_x,forest_y)*np.array([.92,.94,.91])
left=1-ramp((px-500)/39)
west_x=triangular(569+.70*(P-320),530,644)
west_y=np.interp(west_x,*zip(*horizon))+6+T*148
west=sample(west_x,np.minimum(west_y,192))
west_woods=sample(triangular(601+.32*P+.13*row,552,636),triangular(140+.54*row+.06*P,126,169))
west_woods=west_woods*.72+np.array([14,20,23])
west=west*(1-ramp((west_y-121)/51))+west_woods*ramp((west_y-121)/51)
src=src*(1-left)+west*left
# Keep the real eastern buttress and its wooded foot. Only the roof and site in
# the lower panorama are replaced with canopy from the same light and exposure.
edge=np.interp(px,[500,565,635,680,710,750,777,801,850],[175,212,252,256,250,234,208,179,175])
fill=ramp((row-edge-3*np.sin(P*.075)-2.7*np.sin(P*.143))/20)*(1-left)
src=src*(1-fill)+forest*fill
lum=src@np.array([.25,.59,.16])
src=src*.76+lum[...,None]*.24
air=(.14+.11*np.exp(-T*7))[...,None]
src=src*(1-air)+np.array([139,155,165])*air
Image.fromarray(np.clip(src,0,255).astype("uint8")).save(out/"cordillera-rock.jpg",quality=95)

im=Image.open(root/"photos/ridge-at-dusk.jpg").convert("RGB")
im.thumbnail((3200,3200))
rgb=np.asarray(im,dtype=np.float32)/255
scale=(im.width/635,im.height/620)
# Bound the sunlit plazas and strips by their retaining walls. Yellow grass in
# the original reference gets green albedo; the houses keep grey mineral values.
mask=Image.new("L",im.size)
pen=ImageDraw.Draw(mask)
lawns=[[(238,375),(265,373),(277,377),(298,378),(303,384),(326,387),(325,394),(304,398),(268,391),(248,385)],
 [(279,359),(308,362),(324,367),(322,372),(284,369)],
 [(323,368),(346,372),(348,379),(324,376)],[(351,394),(370,395),(371,399),(357,398)],
 [(353,401),(378,399),(380,404),(359,407)],[(375,415),(405,419),(415,425),(389,424)],
 [(185,353),(210,353),(214,357),(183,357)],[(174,360),(211,362),(208,365),(171,363)],
 [(169,366),(203,367),(198,370),(165,370)],[(156,371),(196,373),(191,376),(155,375)],
 [(324,464),(367,451),(461,454),(503,456),(497,463),(388,460)],
 [(532,432),(632,429),(634,453),(528,446)],[(89,488),(131,475),(151,477),(98,493)]]
for poly in lawns:pen.polygon([(round(x*scale[0]),round(y*scale[1])) for x,y in poly],fill=255)
region=np.asarray(mask.filter(ImageFilter.GaussianBlur(1.35)),dtype=np.float32)/255
lum=rgb@np.array([.26,.59,.15],np.float32)
natural=np.clip((rgb[:,:,1]-.70*rgb[:,:,0]-.025)*7.0,0,1)
grass=region*np.clip(natural*1.6+.24,0,1)
yellow=np.clip((rgb[:,:,1]-.68*rgb[:,:,0]-.20*rgb[:,:,2]-.012)*14,0,1)
grass=np.maximum(grass,yellow*np.clip((rgb[:,:,1]-rgb[:,:,2]-.06)*3.3,0,1)*.82)
stone=(np.clip(lum*.72+.10,0,1)[...,None]*np.array([1.025,1.012,.985]))
stone=stone*.93+rgb*.07
green=np.clip(lum*.69+.095,0,1)[...,None]*np.array([.82,.97,.65])
city=stone*(1-grass[...,None])+green*grass[...,None]
sx,sy=np.meshgrid(np.arange(im.width)/scale[0],np.arange(im.height)/scale[1])
west_edge=np.interp(sy,[330,350,375,400,432,463,492,520,620],[205,177,157,147,123,104,83,56,0])
blocks=np.clip((sx-west_edge+5)/10,0,1)
natural_slope=lum[...,None]*np.array([1.04,1.06,.78])*.86+rgb*.12
city=city*blocks[...,None]+natural_slope*(1-blocks[...,None])
Image.fromarray(np.clip(city*255,0,255).astype("uint8")).save(out/"city-albedo.jpg",quality=96)
