from typing import Dict, Tuple, Union
import copy
import numpy as np
import torch
import torch.nn as nn
import torchvision
from .crop_randomizer import CropRandomizer
from maniskill2_learn.utils.diffusion.module_attr_mixin import ModuleAttrMixin
from maniskill2_learn.utils.diffusion.torch import dict_apply, replace_submodules
from maniskill2_learn.networks.modules.cnn_modules.model_getter import get_resnet
from maniskill2_learn.networks.backbones.rl_cnn import CNNBase
from maniskill2_learn.utils.torch import no_grad
from maniskill2_learn.networks.builder import MODELNETWORKS

@MODELNETWORKS.register_module()
class MultiImageObsEncoder(ModuleAttrMixin, CNNBase):
    @no_grad
    def preprocess(self, inputs):
        # assert inputs are channel-first; output is channel-first
        if isinstance(inputs, dict):
            if "rgb" in inputs:
                # inputs images must not have been normalized before
                inputs["rgb"] /= 255.0
                if "depth" in inputs:
                    feature = [inputs["rgb"]]
                    depth = inputs["depth"]
                    if isinstance(depth, torch.Tensor):
                        feature.append(depth.float())
                    elif isinstance(depth, np.ndarray):
                        feature.append(depth.astype(np.float32))
                    else:
                        raise NotImplementedError()
                    inputs["rgbd"] = torch.cat(feature, dim=1)
                    inputs.pop("rgb")
                    inputs.pop("depth")

        return inputs
    
    def __init__(self,
            shape_meta: dict,
            rgb_model: Union[nn.Module, Dict[str,nn.Module]]=get_resnet("resnet18"),
            resize_shape: Union[Tuple[int,int], Dict[str,tuple], None]=None,
            crop_shape: Union[Tuple[int,int], Dict[str,tuple], None]=[76,76],
            random_crop: bool=True,
            # replace BatchNorm with GroupNorm
            use_group_norm: bool=False,
            # use single rgb model for all rgb inputs
            share_rgb_model: bool=True,
            # renormalize rgb input with imagenet normalization
            # assuming input in [0,1]
            imagenet_norm: bool=False
        ):
        """
        Assumes rgb input: B,C,H,W
        Assumes low_dim input: B,D
        """
        super().__init__()

        rgb_keys = list()
        low_dim_keys = list()
        key_model_map = nn.ModuleDict()
        key_transform_map = nn.ModuleDict()
        key_shape_map = dict()

        obs_shape_meta = shape_meta['obs']
        # handle sharing vision backbone
        if share_rgb_model:
            assert isinstance(rgb_model, nn.Module)
            key_model_map['rgb'] = rgb_model
            for key in obs_shape_meta.keys():
                key_model_map[key] = rgb_model
  
        for key, attr in obs_shape_meta.items():
            shape = tuple(attr['shape'])
            key_shape_map[key] = shape
            obs_type = attr["type"]
            if obs_type == 'rgb' or 'rgbd':
                channel = attr.get('channel', 3)
                shape = tuple([channel, *shape])
                key_shape_map[key] = shape
                rgb_keys.append(key)
                # configure model for this key
                this_model = None
                if not share_rgb_model:
                    if isinstance(rgb_model, dict):
                        # have provided model for each key
                        this_model = rgb_model[key]
                    else:
                        assert isinstance(rgb_model, nn.Module)
                        # have a copy of the rgb model
                        this_model = copy.deepcopy(rgb_model)
                
                if this_model is not None:
                    if use_group_norm:
                        this_model = replace_submodules(
                            root_module=this_model,
                            predicate=lambda x: isinstance(x, nn.BatchNorm2d),
                            func=lambda x: nn.GroupNorm(
                                num_groups=x.num_features//16, 
                                num_channels=x.num_features)
                        )
                    key_model_map[key] = this_model

                if obs_type == "rgbd":
                    key_model_map[key].conv1 = torch.nn.Conv2d(4, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
                
                # configure resize
                input_shape = shape
                this_resizer = nn.Identity()
                if resize_shape is not None:
                    if isinstance(resize_shape, dict):
                        h, w = resize_shape[key]
                    else:
                        h, w = resize_shape
                    this_resizer = torchvision.transforms.Resize(
                        size=(h,w)
                    )
                    input_shape = (shape[0],h,w)

                # configure randomizer
                this_randomizer = nn.Identity()
                if crop_shape is not None:
                    if isinstance(crop_shape, dict):
                        h, w = crop_shape[key]
                    else:
                        h, w = crop_shape
                    if random_crop:
                        this_randomizer = CropRandomizer(
                            input_shape=input_shape,
                            crop_height=h,
                            crop_width=w,
                            num_crops=1,
                            pos_enc=False
                        )
                    else:
                        this_normalizer = torchvision.transforms.CenterCrop(
                            size=(h,w)
                        )
                # configure normalizer
                this_normalizer = nn.Identity()
                if imagenet_norm:
                    this_normalizer = torchvision.transforms.Normalize(
                        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
                
                this_transform = nn.Sequential(this_resizer, this_randomizer, this_normalizer)
                key_transform_map[key] = this_transform
            elif type == 'low_dim':
                low_dim_keys.append(key)
            else:
                raise RuntimeError(f"Unsupported obs type: {type}")
        rgb_keys = sorted(rgb_keys)
        low_dim_keys = sorted(low_dim_keys)

        self.shape_meta = shape_meta
        self.key_model_map = key_model_map
        self.key_transform_map = key_transform_map
        self.share_rgb_model = share_rgb_model
        self.rgb_keys = rgb_keys
        self.low_dim_keys = low_dim_keys
        self.key_shape_map = key_shape_map
        self.out_feature_dim = self.output_shape()

        print("fuck!", self.out_feature_dim)

    def forward(self, obs_dict):
        batch_size = None
        features = list()
        # process rgb input
        if self.share_rgb_model:
            # pass all rgb obs to rgb model
            imgs = list()
            for key in self.rgb_keys:
                img = obs_dict[key]
                if isinstance(img, list):
                    img = img[0]
                if batch_size is None:
                    batch_size = img.shape[0]
                else:
                    assert batch_size == img.shape[0]
                print(img.shape)
                if len(img.shape) == 5: # (bs, length, channel, h, w)
                    img = img.reshape(batch_size*img.shape[1],*img.shape[2:])
                print(img.shape)
                assert img.shape[1:] == self.key_shape_map[key]
                img = self.key_transform_map[key](img)
                imgs.append(img)
            # (N*B,C,H,W)
            imgs = torch.cat(imgs, dim=0)
            # (N*B,D)
            feature = self.key_model_map['rgb'](imgs)
            # (N,B,D)
            feature = feature.reshape(-1,batch_size,*feature.shape[1:])
            # (B,N,D)
            feature = torch.moveaxis(feature,0,1)
            # (B,N*D)
            feature = feature.reshape(batch_size,-1)
            features.append(feature)
        else:
            # run each rgb obs to independent models
            for key in self.rgb_keys:
                img = obs_dict[key]
                if isinstance(img, list):
                    img = img[0]
                if batch_size is None:
                    batch_size = img.shape[0]
                else:
                    assert batch_size == img.shape[0]
                if len(img.shape) == 5: # (bs, length, channel, h, w)
                    img = img.reshape(batch_size*img.shape[1],-1)
                assert img.shape[1:] == self.key_shape_map[key]
                img = self.key_transform_map[key](img)
                feature = self.key_model_map[key](img)
                features.append(feature)
        
        # process lowdim input
        for key in self.low_dim_keys:
            data = obs_dict[key]
            if batch_size is None:
                batch_size = data.shape[0]
            else:
                assert batch_size == data.shape[0]
            assert data.shape[1:] == self.key_shape_map[key]
            features.append(data)
        
        # concatenate all features
        result = torch.cat(features, dim=-1)
        return result
    
    @torch.no_grad()
    def output_shape(self):
        example_obs_dict = dict()
        obs_shape_meta = self.shape_meta['obs']
        batch_size = 1
        for key, attr in obs_shape_meta.items():
            shape = self.key_shape_map[key]
            this_obs = torch.zeros(
                (batch_size,) + shape, 
                dtype=self.dtype,
                device=self.device)
            print("kkkkk", this_obs.shape)
            example_obs_dict[key] = this_obs
        example_output = self.forward(example_obs_dict)
        output_shape = list(example_output.shape[1:])
        if len(output_shape) == 1:
            output_shape = output_shape[0]
        return output_shape