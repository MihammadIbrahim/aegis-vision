"""CCTV-specific image augmentations, built with albumentations.

Surveillance footage differs from web photos: low light and strong gamma, heavy JPEG/H.264
compression, sensor noise, motion blur, and a high, tilted viewpoint. The pipelines apply these on
top of the usual flips and crops:

* ``cctv_image_transform``: for single images (Re-ID crops; detector training when the trainer
  lets us inject it).
* ``CctvClipAugmenter``: for video clips. The **same** random parameters are applied to every frame
  of a clip (``ReplayCompose``), so a clip stays temporally consistent; frame-wise random noise
  would teach the model that flicker means violence.

Detector training with ``ultralytics`` keeps its own augmentation stack (mosaic, mixup, HSV,
perspective); with albumentations installed it also applies blur, grey, CLAHE and image
compression automatically. The CCTV-specific values for it live in ``configs/weapon_yolo11.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass

import albumentations as A
import numpy as np


@dataclass(frozen=True)
class CctvAugmentConfig:
    low_light_p: float = 0.5
    gamma_limit: tuple[int, int] = (50, 150)  # <100 darkens
    brightness_contrast_p: float = 0.5
    compression_p: float = 0.5
    jpeg_quality: tuple[int, int] = (25, 90)
    noise_p: float = 0.4
    motion_blur_p: float = 0.3
    motion_blur_limit: tuple[int, int] = (3, 9)
    perspective_p: float = 0.3
    perspective_scale: tuple[float, float] = (0.02, 0.08)
    grey_p: float = 0.1  # many CCTV feeds switch to IR / monochrome at night
    fog_p: float = 0.05


def _transforms(cfg: CctvAugmentConfig, *, geometric: bool) -> list[A.BasicTransform]:
    items: list[A.BasicTransform] = [
        A.RandomGamma(gamma_limit=cfg.gamma_limit, p=cfg.low_light_p),
        A.RandomBrightnessContrast(brightness_limit=(-0.35, 0.15), contrast_limit=(-0.3, 0.2), p=cfg.brightness_contrast_p),
        A.ImageCompression(quality_range=cfg.jpeg_quality, p=cfg.compression_p),
        A.OneOf([A.ISONoise(p=1.0), A.GaussNoise(std_range=(0.02, 0.1), p=1.0)], p=cfg.noise_p),
        A.MotionBlur(blur_limit=cfg.motion_blur_limit, p=cfg.motion_blur_p),
        A.ToGray(p=cfg.grey_p),
        A.RandomFog(p=cfg.fog_p),
    ]
    if geometric:
        items.append(A.Perspective(scale=cfg.perspective_scale, keep_size=True, p=cfg.perspective_p))
    return items


def cctv_image_transform(cfg: CctvAugmentConfig | None = None, *, geometric: bool = False) -> A.Compose:
    """Photometric (and optionally perspective) CCTV augmentation for one image (RGB or BGR uint8)."""
    return A.Compose(_transforms(cfg or CctvAugmentConfig(), geometric=geometric))


class CctvClipAugmenter:
    """Applies one random draw of the CCTV augmentations to all frames of a clip."""

    def __init__(self, cfg: CctvAugmentConfig | None = None, *, geometric: bool = True) -> None:
        self._compose = A.ReplayCompose(_transforms(cfg or CctvAugmentConfig(), geometric=geometric))

    def __call__(self, frames: np.ndarray) -> np.ndarray:
        """``frames``: uint8 array (T, H, W, 3). Returns the augmented clip with the same shape."""
        if frames.ndim != 4 or frames.shape[-1] != 3 or frames.dtype != np.uint8:
            raise ValueError("frames must be uint8 with shape (T, H, W, 3)")
        first = self._compose(image=frames[0])
        replay = first["replay"]
        out = np.empty_like(frames)
        out[0] = first["image"]
        for index in range(1, frames.shape[0]):
            out[index] = A.ReplayCompose.replay(replay, image=frames[index])["image"]
        return out
