"""Infer relative surface relief from the dusk view, prior to metric registration."""
from pathlib import Path
import numpy as np
import onnxruntime as ort
from PIL import Image

root=Path(__file__).resolve().parents[1]
im=Image.open(root/"photos/ridge-at-dusk.jpg").convert("RGB")
opt=ort.SessionOptions();opt.intra_op_num_threads=6;opt.inter_op_num_threads=1
sess=ort.InferenceSession(str(root/"tmp/depth-anything-v2-small.onnx"),opt,providers=["CPUExecutionProvider"])
im=im.resize((812,784),Image.Resampling.BICUBIC)
rgb=np.asarray(im,dtype=np.float32)/255
rgb=(rgb-np.float32([.485,.456,.406]))/np.float32([.229,.224,.225])
data=sess.run(None,{sess.get_inputs()[0].name:rgb.transpose(2,0,1)[None]})[0].squeeze()
np.save(root/"build/citadel-disparity.npy",data)
for u,v in [(300,163),(205,356),(303,366),(393,368),(307,391),(204,400),(350,427),(280,452),(280,479),(400,469),(260,500)]:
    print((u,v),round(float(data[round(v/620*(data.shape[0]-1)),round(u/635*(data.shape[1]-1))]),3))
