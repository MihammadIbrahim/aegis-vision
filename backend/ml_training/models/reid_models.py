"""Person re-identification embedders.

Two backbones, one head:

* **OSNet-x1.0** (Zhou et al., ICCV 2019): omni-scale residual blocks with unified aggregation gates;
  512-D output; 2.2 M parameters, built here layer by layer. Module names follow the authors'
  ``torchreid`` implementation, so their ImageNet or Re-ID pre-trained ``osnet_x1_0_*.pth`` files load
  with ``load_pretrained``.
* **ResNet50-IBN-a** (Pan et al., ECCV 2018): torchvision ResNet-50 with ImageNet weights, instance
  norm on half the channels of the first three stages (robust to lighting and camera style), and the
  last stride set to 1 (standard in Re-ID). 2048-D output, reduced to 512-D by the head.

**BNNeck** (Luo et al., CVPRW 2019): ``features → [Linear to 512] → BatchNorm (no bias) → classifier``.
The triplet loss sees the features before the BN; the classifier sees the features after it; the
runtime embedding is the BN output, L2-normalised. The ONNX export returns that normalised vector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn
from torchvision.models import ResNet50_Weights, resnet50

BACKBONES = ("osnet_x1_0", "osnet_x0_5", "resnet50_ibn_a")


# --- OSNet ------------------------------------------------------------------------------------------------------


class ConvLayer(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, k: int, stride: int = 1, padding: int = 0, groups: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, k, stride=stride, padding=padding, bias=False, groups=groups)
        self.bn = nn.BatchNorm2d(out_ch)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.bn(self.conv(x)))


class Conv1x1(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, 1, stride=stride, bias=False)
        self.bn = nn.BatchNorm2d(out_ch)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.bn(self.conv(x)))


class Conv1x1Linear(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, 1, stride=stride, bias=False)
        self.bn = nn.BatchNorm2d(out_ch)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.bn(self.conv(x))


class LightConv3x3(nn.Module):
    """1×1 conv followed by a depthwise 3×3 conv (the Lite 3×3 layer of the paper)."""

    def __init__(self, in_ch: int, out_ch: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 1, bias=False)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False, groups=out_ch)
        self.bn = nn.BatchNorm2d(out_ch)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.bn(self.conv2(self.conv1(x))))


class ChannelGate(nn.Module):
    """The unified aggregation gate: a per-channel sigmoid weight from global average pooling."""

    def __init__(self, in_ch: int, reduction: int = 16) -> None:
        super().__init__()
        self.global_avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc1 = nn.Conv2d(in_ch, in_ch // reduction, 1, bias=True)
        self.norm1 = None
        self.relu = nn.ReLU(inplace=True)
        self.fc2 = nn.Conv2d(in_ch // reduction, in_ch, 1, bias=True)
        self.gate_activation = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        g = self.global_avgpool(x)
        g = self.relu(self.fc1(g))
        g = self.gate_activation(self.fc2(g))
        return x * g


class OSBlock(nn.Module):
    """Omni-scale residual block: four streams of 1, 2, 3 and 4 stacked Lite 3×3 layers (receptive
    fields 3, 5, 7, 9), fused by the shared aggregation gate, plus a residual connection."""

    def __init__(self, in_ch: int, out_ch: int, bottleneck_reduction: int = 4) -> None:
        super().__init__()
        mid = out_ch // bottleneck_reduction
        self.conv1 = Conv1x1(in_ch, mid)
        self.conv2a = LightConv3x3(mid, mid)
        self.conv2b = nn.Sequential(LightConv3x3(mid, mid), LightConv3x3(mid, mid))
        self.conv2c = nn.Sequential(LightConv3x3(mid, mid), LightConv3x3(mid, mid), LightConv3x3(mid, mid))
        self.conv2d = nn.Sequential(LightConv3x3(mid, mid), LightConv3x3(mid, mid), LightConv3x3(mid, mid), LightConv3x3(mid, mid))
        self.gate = ChannelGate(mid)
        self.conv3 = Conv1x1Linear(mid, out_ch)
        self.downsample = Conv1x1Linear(in_ch, out_ch) if in_ch != out_ch else None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        x1 = self.conv1(x)
        x2 = self.gate(self.conv2a(x1)) + self.gate(self.conv2b(x1)) + self.gate(self.conv2c(x1)) + self.gate(self.conv2d(x1))
        x3 = self.conv3(x2)
        if self.downsample is not None:
            identity = self.downsample(identity)
        return F.relu(x3 + identity)


class OSNet(nn.Module):
    """``osnet_x1_0``: channels (64, 256, 384, 512), 2 blocks per stage. ``feature_dim`` = 512."""

    def __init__(self, channels: Sequence[int] = (64, 256, 384, 512), blocks: Sequence[int] = (2, 2, 2), feature_dim: int = 512) -> None:
        super().__init__()
        self.conv1 = ConvLayer(3, channels[0], 7, stride=2, padding=3)
        self.maxpool = nn.MaxPool2d(3, stride=2, padding=1)
        self.conv2 = self._stage(channels[0], channels[1], blocks[0], reduce=True)
        self.conv3 = self._stage(channels[1], channels[2], blocks[1], reduce=True)
        self.conv4 = self._stage(channels[2], channels[3], blocks[2], reduce=False)
        self.conv5 = Conv1x1(channels[3], channels[3])
        self.global_avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(nn.Linear(channels[3], feature_dim), nn.BatchNorm1d(feature_dim), nn.ReLU(inplace=True))
        self.feature_dim = feature_dim
        self._init_weights()

    @staticmethod
    def _stage(in_ch: int, out_ch: int, blocks: int, *, reduce: bool) -> nn.Sequential:
        layers: list[nn.Module] = [OSBlock(in_ch, out_ch)]
        layers += [OSBlock(out_ch, out_ch) for _ in range(blocks - 1)]
        if reduce:
            layers.append(nn.Sequential(Conv1x1(out_ch, out_ch), nn.AvgPool2d(2, stride=2)))
        return nn.Sequential(*layers)

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.BatchNorm1d)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.maxpool(self.conv1(x))
        x = self.conv5(self.conv4(self.conv3(self.conv2(x))))
        x = self.global_avgpool(x).flatten(1)
        return self.fc(x)


# --- ResNet50-IBN-a ----------------------------------------------------------------------------------------------


class IBN(nn.Module):
    """Instance norm on the first half of the channels, batch norm on the rest (IBN-a)."""

    def __init__(self, planes: int) -> None:
        super().__init__()
        self.half = planes // 2
        self.IN = nn.InstanceNorm2d(self.half, affine=True)
        self.BN = nn.BatchNorm2d(planes - self.half)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        a, b = torch.split(x, [self.half, x.shape[1] - self.half], dim=1)
        return torch.cat([self.IN(a.contiguous()), self.BN(b.contiguous())], dim=1)


class ResNet50IBNa(nn.Module):
    feature_dim = 2048

    def __init__(self, *, pretrained: bool = True, last_stride: int = 1) -> None:
        super().__init__()
        net = resnet50(weights=ResNet50_Weights.IMAGENET1K_V2 if pretrained else None)
        for layer in (net.layer1, net.layer2, net.layer3):
            for block in layer:
                planes = block.bn1.num_features
                ibn = IBN(planes)
                if pretrained:  # keep the BatchNorm half's statistics from ImageNet
                    ibn.BN.weight.data.copy_(block.bn1.weight.data[ibn.half :])
                    ibn.BN.bias.data.copy_(block.bn1.bias.data[ibn.half :])
                    ibn.BN.running_mean.copy_(block.bn1.running_mean[ibn.half :])
                    ibn.BN.running_var.copy_(block.bn1.running_var[ibn.half :])
                block.bn1 = ibn
        if last_stride == 1:
            net.layer4[0].conv2.stride = (1, 1)
            net.layer4[0].downsample[0].stride = (1, 1)
        self.body = nn.Sequential(net.conv1, net.bn1, net.relu, net.maxpool, net.layer1, net.layer2, net.layer3, net.layer4)
        self.pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.body(x)).flatten(1)


# --- BNNeck head and full model ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReidModelSpec:
    backbone: str = "osnet_x1_0"
    num_classes: int = 751
    embed_dim: int = 512
    pretrained: bool = True  # ImageNet weights for ResNet50-IBN; for OSNet pass ``pretrained_path``
    pretrained_path: str | None = None
    height: int = 256
    width: int = 128

    def __post_init__(self) -> None:
        if self.backbone not in BACKBONES:
            raise ValueError(f"backbone must be one of {BACKBONES}, got {self.backbone!r}")


class BNNeck(nn.Module):
    def __init__(self, in_features: int, embed_dim: int, num_classes: int) -> None:
        super().__init__()
        self.reduce = nn.Linear(in_features, embed_dim, bias=False) if in_features != embed_dim else nn.Identity()
        self.bottleneck = nn.BatchNorm1d(embed_dim)
        self.bottleneck.bias.requires_grad_(False)  # no shift, as in the BNNeck paper
        self.classifier = nn.Linear(embed_dim, num_classes, bias=False)
        nn.init.normal_(self.classifier.weight, std=0.001)
        if isinstance(self.reduce, nn.Linear):
            nn.init.kaiming_normal_(self.reduce.weight, mode="fan_out")

    def forward(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        pre = self.reduce(features)
        post = self.bottleneck(pre)
        return self.classifier(post), pre, post


class ReidModel(nn.Module):
    """Training: returns ``(logits, triplet_features, embedding)``. Eval: the L2-normalised embedding."""

    def __init__(self, spec: ReidModelSpec) -> None:
        super().__init__()
        self.spec = spec
        if spec.backbone.startswith("osnet"):
            channels = (64, 256, 384, 512) if spec.backbone == "osnet_x1_0" else (32, 128, 192, 256)
            self.backbone: nn.Module = OSNet(channels=channels, feature_dim=512)
            in_features = 512
            if spec.pretrained_path:
                load_pretrained(self.backbone, Path(spec.pretrained_path))
        else:
            self.backbone = ResNet50IBNa(pretrained=spec.pretrained)
            in_features = ResNet50IBNa.feature_dim
        self.neck = BNNeck(in_features, spec.embed_dim, spec.num_classes)
        self.embed_dim = spec.embed_dim

    def forward(self, images: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor] | torch.Tensor:
        features = self.backbone(images)
        logits, pre, post = self.neck(features)
        if self.training:
            return logits, pre, post
        return F.normalize(post, dim=1)

    @torch.no_grad()
    def embed(self, images: torch.Tensor) -> torch.Tensor:
        was_training = self.training
        self.eval()
        try:
            return self.forward(images)  # type: ignore[return-value]
        finally:
            self.train(was_training)


def load_pretrained(module: nn.Module, path: Path) -> tuple[int, int]:
    """Loads matching tensors from a checkpoint (torchreid or this project), ignoring the classifier.
    Returns (loaded, skipped) counts."""
    state = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if isinstance(state, dict) and "model" in state and isinstance(state["model"], dict):
        state = state["model"]
    own = module.state_dict()
    loaded, skipped = 0, 0
    new_state = {}
    for key, value in state.items():
        name = key.removeprefix("module.").removeprefix("backbone.")
        if name in own and own[name].shape == value.shape and not name.startswith("classifier"):
            new_state[name] = value
            loaded += 1
        else:
            skipped += 1
    module.load_state_dict({**own, **new_state})
    return loaded, skipped


def build_reid_model(spec: ReidModelSpec) -> ReidModel:
    return ReidModel(spec)


class ReidOnnxWrapper(nn.Module):
    """Export-time wrapper: images → L2-normalised 512-D embeddings."""

    def __init__(self, model: ReidModel) -> None:
        super().__init__()
        self.model = model
        self.model.eval()

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        features = self.model.backbone(images)
        _, _, post = self.model.neck(features)
        return F.normalize(post, dim=1)
