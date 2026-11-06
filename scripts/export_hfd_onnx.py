"""Run with the HFD worker Python; export official fixed-size networks locally."""
import argparse
from pathlib import Path
import openpifpaf
import torch
from openpifpaf.export_onnx import apply, check


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--weights',default='features/fall_risk/hfd_model/models/shufflenetv2k16.pkl')
    parser.add_argument('--size',default='513x289')
    args=parser.parse_args()
    path=Path(args.weights).resolve()
    if not path.is_file():raise FileNotFoundError(path)
    width,height=map(int,args.size.split('x'))
    if width%16!=1 or height%16!=1:raise ValueError('ONNX size must be stride16 plus1')
    out=path.with_name(f'{path.stem}-{width}x{height}.onnx')
    if out.exists():raise FileExistsError(out)
    torch.set_num_threads(4)
    openpifpaf.network.Factory.checkpoint=str(path)
    model,_=openpifpaf.network.Factory().factory()
    model.eval()
    apply(model,str(out),verbose=False,input_w=width,input_h=height)
    check(str(out))
    print(out,flush=True)


if __name__=='__main__':main()
