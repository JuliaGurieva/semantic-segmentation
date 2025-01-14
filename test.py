import torch
import argparse
import yaml
import math
import cv2
import numpy as np
from torch import Tensor
from torch.nn import functional as F
from pathlib import Path
from torchvision import io
from torchvision import transforms as T
from semseg.models import *
from semseg.datasets import *
from semseg.utils.utils import timer


class SemSeg:
    def __init__(self, cfg) -> None:
        # inference device cuda or cpu
        self.device = torch.device(cfg['DEVICE'])
        # get dataset classes' colors
        self.palette = eval(cfg['DATASET']['NAME']).PALETTE
        # initialize the model and load weights and send to device
        self.model = eval(cfg['MODEL']['NAME'])(cfg['MODEL']['BACKBONE'], len(self.palette))
        self.model.load_state_dict(torch.load(cfg['TEST']['MODEL_PATH'], map_location='cpu'))
        self.model = self.model.to(self.device)
        self.model.eval()
        # preprocess parameters
        self.size = cfg['TEST']['IMAGE_SIZE']
        self.norm = T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))

    def resize(self, image: Tensor) -> Tensor:
        H, W = image.shape[1:]
        # scale the short side of image to target size
        scale_factor = self.size[0] / min(H, W)
        nH, nW = round(H*scale_factor), round(W*scale_factor)
        # make it divisible by model stride
        nH, nW = int(math.ceil(nH / 32)) * 32, int(math.ceil(nW / 32)) * 32
        # resize the image
        image = T.Resize((nH, nW))(image)
        return image

    def preprocess(self, image: Tensor) -> Tensor:
        # resize the image to inference image size
        img = self.resize(image)
        # img = img[[2, 1, 0], ...]     # RGB to BGR (for hardnet)
        # scale to [0.0, 1.0]
        img = img.float() / 255
        # normalize
        img = self.norm(img)
        # add batch size and send to device
        img = img.unsqueeze(0).to(self.device)
        return img

    def postprocess(self, seg_map: Tensor, orig_size: list) -> Tensor:
        # resize to original image size
        seg_map = F.interpolate(seg_map, size=orig_size, mode='bilinear', align_corners=True)
        # get segmentation map (value being 0 to num_classes)
        seg_map = seg_map.softmax(dim=1).argmax(dim=1).to(int)
        # convert segmentation map to color map
        seg_map = self.palette[seg_map]
        return seg_map.squeeze().cpu().permute(2, 0, 1)

    @torch.inference_mode()
    @timer
    def model_forward(self, img: Tensor) -> Tensor:
        return self.model(img)
        
    def predict(self, img_fname: str, overlay: bool) -> Tensor:
        image = io.read_image(img_fname)
        img = self.preprocess(image)
        seg_map = self.model_forward(img)
        seg_map = self.postprocess(seg_map, image.shape[-2:])
        if overlay: seg_map = (image * 0.4) + (seg_map * 0.6)
        return seg_map.to(torch.uint8)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cfg', type=str, default='config.yaml')
    args = parser.parse_args()

    with open(args.cfg) as f:
        cfg = yaml.load(f, Loader=yaml.SafeLoader)

    test_file = Path(cfg['TEST']['FILE'])
    save_dir = Path(cfg['SAVE_DIR']) / 'test_results'
    save_dir.mkdir(exist_ok=True)
    
    semseg = SemSeg(cfg)

    if test_file.is_file():
        print(f'Inferencing {test_file} ...')
        segmap = semseg.predict(str(test_file), cfg['TEST']['OVERLAY'])
        io.write_png(segmap, str(save_dir / f"{str(test_file.stem)}.png"))
    else:
        files = test_file.glob('*.*')
        for file in files:
            print(f'Inferencing {file} ...')
            segmap = semseg.predict(str(file), cfg['TEST']['OVERLAY'])
            io.write_png(segmap, str(save_dir / f"{str(file.stem)}.png"))

    print(f"Results saved in {save_dir}")
    ref_dir = Path(cfg['SAVE_DIR']) / 'reference_test_results'
    
    #reference:
    print()
    print('Matching the results:')
    flags = True
    if test_file.is_file():
        ref = cv2.imread(ref_dir / test_file)
        result = cv2.imread(save_dir / test_file)
        flags = flags and (hash(ref.tobytes()) == hash(result.tobytes()))
    else:
        files = test_file.glob('*.*')
        for file in files:
            ref = cv2.imread(str(ref_dir / f"{str(file.stem)}.png"))
            result = cv2.imread(str(save_dir / f"{str(file.stem)}.png"))
            equal = (hash(ref.tobytes()) == hash(result.tobytes()))
            flags = flags and equal
            if equal:
                print(f" image {str(file.stem)}.png: results are equal")
            else:
                print(f" image {str(file.stem)}.png: results are not equal")
    if flags is True:
        print('Ok!')
