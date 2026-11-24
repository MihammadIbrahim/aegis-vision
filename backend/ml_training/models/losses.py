"""Losses for metric learning (Re-ID) and classification.

* ``CrossEntropyLabelSmooth``: softmax cross-entropy with label smoothing ε (Szegedy et al.).
* ``BatchHardTripletLoss``: for each anchor in a P×K batch, the hardest positive (farthest same
  identity) and the hardest negative (closest other identity); margin or soft-margin (Hermans et al.,
  "In Defense of the Triplet Loss").
* ``CenterLoss``: pulls features towards a learned centre per identity (Wen et al.); the centres
  have their own optimizer with a higher learning rate, as in the original recipe.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn


class CrossEntropyLabelSmooth(nn.Module):
    def __init__(self, num_classes: int, epsilon: float = 0.1) -> None:
        super().__init__()
        if not 0 <= epsilon < 1:
            raise ValueError("epsilon must be in [0, 1)")
        self.num_classes = num_classes
        self.epsilon = epsilon

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        log_probs = F.log_softmax(logits.float(), dim=1)
        with torch.no_grad():
            smooth = torch.full_like(log_probs, self.epsilon / (self.num_classes - 1) if self.num_classes > 1 else 0.0)
            smooth.scatter_(1, targets.unsqueeze(1), 1 - self.epsilon)
        return (-smooth * log_probs).sum(dim=1).mean()


def pairwise_euclidean(a: torch.Tensor, b: torch.Tensor, eps: float = 1e-12) -> torch.Tensor:
    """Euclidean distances between every row of ``a`` and ``b``, numerically safe for the gradient."""
    squared = a.pow(2).sum(1, keepdim=True) + b.pow(2).sum(1).unsqueeze(0) - 2 * a @ b.t()
    return squared.clamp_min(eps).sqrt()


class BatchHardTripletLoss(nn.Module):
    def __init__(self, margin: float | None = 0.3, *, normalize_features: bool = False) -> None:
        """``margin=None`` uses the soft-margin ``log(1 + exp(d_ap - d_an))`` form."""
        super().__init__()
        self.margin = margin
        self.normalize = normalize_features

    def forward(self, features: torch.Tensor, targets: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
        feats = F.normalize(features.float(), dim=1) if self.normalize else features.float()
        dist = pairwise_euclidean(feats, feats)
        same = targets.unsqueeze(0) == targets.unsqueeze(1)
        eye = torch.eye(len(targets), dtype=torch.bool, device=targets.device)
        positive_mask = same & ~eye
        negative_mask = ~same
        if not positive_mask.any(dim=1).all() or not negative_mask.any(dim=1).all():
            raise ValueError("every sample needs at least one positive and one negative in the batch (use PKSampler)")
        hardest_positive = dist.masked_fill(~positive_mask, float("-inf")).max(dim=1).values
        hardest_negative = dist.masked_fill(~negative_mask, float("inf")).min(dim=1).values
        if self.margin is None:
            loss = F.softplus(hardest_positive - hardest_negative).mean()
        else:
            loss = F.relu(hardest_positive - hardest_negative + self.margin).mean()
        with torch.no_grad():
            stats = {
                "triplet_active_fraction": float((hardest_positive + (self.margin or 0.0) > hardest_negative).float().mean()),
                "mean_hardest_positive": float(hardest_positive.mean()),
                "mean_hardest_negative": float(hardest_negative.mean()),
            }
        return loss, stats


class CenterLoss(nn.Module):
    def __init__(self, num_classes: int, feature_dim: int) -> None:
        super().__init__()
        self.centers = nn.Parameter(torch.randn(num_classes, feature_dim))

    def forward(self, features: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        centers = self.centers[targets]
        return 0.5 * (features.float() - centers).pow(2).sum(dim=1).mean()


class ReidCriterion(nn.Module):
    """``CE(label smoothing) + triplet_weight · triplet + center_weight · center``."""

    def __init__(
        self,
        num_classes: int,
        feature_dim: int,
        *,
        label_smoothing: float = 0.1,
        triplet_margin: float | None = 0.3,
        triplet_weight: float = 1.0,
        center_weight: float = 0.0005,
    ) -> None:
        super().__init__()
        self.ce = CrossEntropyLabelSmooth(num_classes, label_smoothing)
        self.triplet = BatchHardTripletLoss(triplet_margin)
        self.center = CenterLoss(num_classes, feature_dim) if center_weight > 0 else None
        self.triplet_weight = triplet_weight
        self.center_weight = center_weight

    def forward(self, logits: torch.Tensor, features: torch.Tensor, targets: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
        ce = self.ce(logits, targets)
        triplet, stats = self.triplet(features, targets)
        total = ce + self.triplet_weight * triplet
        parts = {"loss_ce": ce.item(), "loss_triplet": triplet.item(), **stats}
        if self.center is not None:
            center = self.center(features, targets)
            total = total + self.center_weight * center
            parts["loss_center"] = center.item()
        return total, parts
