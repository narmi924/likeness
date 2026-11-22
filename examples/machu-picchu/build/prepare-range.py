"""Remove the near massif from the distant range so moving reveals the range behind it."""
from pathlib import Path
import numpy as np
from PIL import Image

root=Path(__file__).resolve().parents[1]
photo=np.asarray(Image.open(root/"photos/ridge-at-dusk.jpg").convert("RGB").resize((1270,1240))).copy()
profile=[(0,438),(45,363),(86,301),(116,273),(139,268),(174,305),(208,274),
         (244,205),(279,164),(300,157),(319,162),(344,200),(372,225),(404,245),
         (470,257),(530,277),(579,338),(635,362)]
outline=2*np.interp(np.arange(1270)/2,*zip(*profile))
for y in range(1240):
    usable=np.where(y < outline-9)[0]
    if len(usable)>4:
        missing=np.where(y >= outline-9)[0]
        for c in range(3):
            photo[y,missing,c]=np.interp(missing,usable,photo[y,usable,c])
    elif y:
        photo[y]=photo[y-1]
W,H=2500,1432
v,u=np.mgrid[0:H,0:W]
px=(u/(W-1)*1640-500)*2
px=1269-abs(np.mod(px,2538)-1269)
py=np.clip((v/(H-1)*940-340)*2,0,1239)
Image.fromarray(photo[py.astype(int),px.astype(int)]).save(root/"build/range-color.jpg",quality=94)
