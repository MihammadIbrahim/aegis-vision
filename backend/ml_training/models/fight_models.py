"""Spatio-temporal classifiers for fight / violence detection.

Backbones come from ``torchvision.models.video`` with Kinetics-400 pre-training:

| name          | model                              | params | notes                                     |
|---------------|------------------------------------|--------|-------------------------------------------|
| ``swin3d_t``  | Video Swin Transformer, tiny       | 28 M   | default; best accuracy/compute trade-off  |
| ``swin3d_s``  | Video Swin Transformer, small      | 50 M   | more capacity, RTX 4090 recommended       |
| ``mvit_v2_s`` | Multiscale ViT v2, small           | 34 M   | TimeSformer-class attention model         |
| ``r2plus1d_18`` | (2+1)D ResNet-18                 | 31 M   | fastest on CPU; sanity baseline           |

Why torchvision and not ``pytorchvideo``: pytorchvideo's last release (0.1.5, 2022) imports
``torchvision.transforms.functional_tensor``, removed in torchvision 0.17, so it does not import
with the torch 2.x stack used here. torchvision ships the same Video Swin architecture with
Kinetics-400 weights, and it exports to ONNX cleanly.

Input: ``B × 3 × T × H × W`` float32, Kinetics-normalised (``data_utils.video_clips.to_tensor``).
Output: logits over ``num_classes`` (2: normal, violence). ``violence_probability`` is softmax[:, 1].
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torchvision.models import video as tv_video

BACKBONES = ("swin3d_t", "swin3d_s", "mvit_v2_s", "r2plus1d_18")


@dataclass(frozen=True)
class FightModelSpec:
    backbone: str = "swin3d_t"
    num_classes: int = 2
    pretrained: bool = True
    dropout: float = 0.3
    clip_len: int = 16
    size: int = 160

    def __post_init__(self) -> None:
        if self.backbone not in BACKBONES:
            raise ValueError(f"backbone must be one of {BACKBONES}, got {self.backbone!r}")


class FightClassifier(nn.Module):
    def __init__(self, spec: FightModelSpec) -> None:
        super().__init__()
        self.spec = spec
        weights = None
        if spec.pretrained:
            weights = {
                "swin3d_t": tv_video.Swin3D_T_Weights.KINETICS400_V1,
                "swin3d_s": tv_video.Swin3D_S_Weights.KINETICS400_V1,
                "mvit_v2_s": tv_video.MViT_V2_S_Weights.KINETICS400_V1,
                "r2plus1d_18": tv_video.R2Plus1D_18_Weights.KINETICS400_V1,
            }[spec.backbone]
        builder = getattr(tv_video, spec.backbone)
        self.backbone: nn.Module = builder(weights=weights)
        in_features = self._replace_head(spec.dropout, spec.num_classes)
        self.in_features = in_features

    def _replace_head(self, dropout: float, num_classes: int) -> int:
        name = self.spec.backbone
        if name.startswith("swin3d"):
            in_features = self.backbone.head.in_features  # type: ignore[union-attr]
            self.backbone.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(in_features, num_classes))  # type: ignore[assignment]
        elif name.startswith("mvit"):
            linear = self.backbone.head[1]  # type: ignore[index]
            in_features = linear.in_features
            self.backbone.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(in_features, num_classes))  # type: ignore[assignment]
        else:  # r2plus1d_18
            in_features = self.backbone.fc.in_features  # type: ignore[union-attr]
            self.backbone.fc = nn.Sequential(nn.Dropout(dropout), nn.Linear(in_features, num_classes))  # type: ignore[assignment]
        return int(in_features)

    def forward(self, clips: torch.Tensor) -> torch.Tensor:
        if not torch.jit.is_tracing() and (clips.ndim != 5 or clips.shape[1] != 3):
            raise ValueError(f"expected clips of shape (B, 3, T, H, W), got {tuple(clips.shape)}")
        return self.backbone(clips)

    @torch.no_grad()
    def violence_probability(self, clips: torch.Tensor) -> torch.Tensor:
        return torch.softmax(self.forward(clips), dim=1)[:, 1]

    def parameter_groups(self, *, backbone_lr_scale: float = 0.1) -> list[dict[str, object]]:
        """A lower learning rate for the pre-trained trunk than for the new head."""
        head_names = ("backbone.head", "backbone.fc")
        head, trunk = [], []
        for name, param in self.named_parameters():
            if not param.requires_grad:
                continue
            (head if name.startswith(head_names) else trunk).append(param)
        return [{"params": trunk, "lr_scale": backbone_lr_scale}, {"params": head, "lr_scale": 1.0}]


def build_fight_model(spec: FightModelSpec) -> FightClassifier:
    return FightClassifier(spec)


class FightOnnxWrapper(nn.Module):
    """Export-time wrapper: returns softmax probabilities, so the runtime does not need to know the
    class order beyond the manifest's labels."""

    def __init__(self, model: FightClassifier) -> None:
        super().__init__()
        self.model = model

    def forward(self, clips: torch.Tensor) -> torch.Tensor:
        return torch.softmax(self.model(clips), dim=1)


# --- ONNX export support for Video Swin ------------------------------------------------------------------------


def _constant_attention_mask_3d(x: torch.Tensor, size_dhw: tuple[int, int, int], window_size: tuple[int, int, int], shift_size: tuple[int, int, int]) -> torch.Tensor:
    """Same mask as ``torchvision.models.video.swin_transformer._compute_attention_mask_3d``, built in
    NumPy. torchvision writes it with in-place tensor slice assignments, which the ONNX tracer turns
    into ScatterND nodes with data-dependent shapes that ONNX Runtime rejects. For a fixed input size
    the mask is a constant, so computing it outside the graph is exact and export-safe."""
    import numpy as np

    mask = np.zeros(tuple(int(s) for s in size_dhw), dtype=np.float32)
    slices = [((0, -window_size[i]), (-window_size[i], -shift_size[i]), (-shift_size[i], None)) for i in range(3)]
    count = 0
    for d in slices[0]:
        for h in slices[1]:
            for w in slices[2]:
                mask[d[0] : d[1], h[0] : h[1], w[0] : w[1]] = count
                count += 1
    num_windows = (size_dhw[0] // window_size[0]) * (size_dhw[1] // window_size[1]) * (size_dhw[2] // window_size[2])
    mask = mask.reshape(size_dhw[0] // window_size[0], window_size[0], size_dhw[1] // window_size[1], window_size[1], size_dhw[2] // window_size[2], window_size[2])
    mask = mask.transpose(0, 2, 4, 1, 3, 5).reshape(num_windows, window_size[0] * window_size[1] * window_size[2])
    diff = mask[:, None, :] - mask[:, :, None]
    out = np.where(diff != 0, -100.0, 0.0).astype(np.float32)
    return x.new_tensor(out)


class export_friendly_swin:
    """Context manager: while active, Video Swin builds its shifted-window masks as constants."""

    def __enter__(self) -> None:
        from torchvision.models.video import swin_transformer as module

        self._module, self._original = module, module._compute_attention_mask_3d
        module._compute_attention_mask_3d = _constant_attention_mask_3d  # type: ignore[assignment]

    def __exit__(self, *exc: object) -> None:
        self._module._compute_attention_mask_3d = self._original  # type: ignore[assignment]
