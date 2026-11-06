"""Numerical field parity before selecting an exported OpenPifPaf network."""
import argparse
import json
from pathlib import Path
import numpy as np
import onnxruntime as ort
import openpifpaf
import torch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--weights',default='features/fall_risk/hfd_model/models/shufflenetv2k16.pkl')
    parser.add_argument('--size',default='513x289')
    parser.add_argument('--output',required=True)
    args=parser.parse_args();width,height=map(int,args.size.split('x'))
    torch.set_num_threads(4);torch.manual_seed(0)
    openpifpaf.network.Factory.checkpoint=str(Path(args.weights).resolve())
    model,_=openpifpaf.network.Factory().factory();model.eval()
    openpifpaf.network.heads.CompositeField3.inplace_ops=False
    path=Path(args.weights).with_name(f'{Path(args.weights).stem}-{width}x{height}.onnx')
    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
    records=[]
    for case in range(2):
        tensor=torch.zeros(1,3,height,width) if case==0 else torch.randn(1,3,height,width)
        with torch.no_grad():reference=model(tensor)
        actual=session.run(None,{'input_batch':tensor.numpy()})
        for name,left,right in zip(('cif','caf'),reference,actual):
            target=left.numpy()
            np.testing.assert_allclose(right,target,rtol=1e-3,atol=1e-3)
            records.append(dict(case=case,field=name,max_abs_error=float(np.max(np.abs(right-target)))))
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(dict(status='PASS',size=args.size,tolerance=dict(rtol=.001,atol=.001),records=records),indent=2),encoding='utf-8')
    print(out)


if __name__=='__main__':main()
