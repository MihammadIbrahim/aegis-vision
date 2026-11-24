"""Re-identification evaluation: CMC (Rank-k) and mAP under the standard Market-1501 protocol, and
the calibration of cosine-similarity bands for the runtime.

Protocol (Zheng et al., 2015), as implemented in the reference evaluation code: for each query,
gallery images with the **same person id and the same camera** are removed (they are the same
tracklet, not a re-identification), junk images (pid −1) are removed, and the remaining ranked list
gives the CMC curve and the average precision. Queries with no valid match are skipped.
``cuhk03_protocol=True`` uses single-gallery-shot CMC (one random gallery image per identity and
camera, averaged over repeats), the convention for CUHK03.

Bands: the runtime labels a candidate ``LIKELY_MATCH``, ``UNCERTAIN`` or ``NO_RELIABLE_MATCH`` from
its cosine similarity. ``calibrate_bands`` picks the two thresholds from validation pairs so that
``LIKELY_MATCH`` reaches a target precision (default 0.95) and ``UNCERTAIN`` a lower one (0.5). A
``LIKELY_MATCH`` is still a candidate for human review, never a confirmed identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def cosine_distance(query: np.ndarray, gallery: np.ndarray) -> np.ndarray:
    q = query / np.clip(np.linalg.norm(query, axis=1, keepdims=True), 1e-12, None)
    g = gallery / np.clip(np.linalg.norm(gallery, axis=1, keepdims=True), 1e-12, None)
    return 1.0 - q @ g.T


@dataclass
class ReidReport:
    cmc: dict[int, float]
    mAP: float
    queries_evaluated: int
    queries_skipped: int

    def as_dict(self) -> dict[str, object]:
        return {
            **{f"rank{k}": round(v, 4) for k, v in self.cmc.items()},
            "mAP": round(self.mAP, 4),
            "queries_evaluated": self.queries_evaluated,
            "queries_skipped": self.queries_skipped,
        }


def evaluate_rank(
    distmat: np.ndarray,
    q_pids: Sequence[int],
    g_pids: Sequence[int],
    q_camids: Sequence[int],
    g_camids: Sequence[int],
    *,
    ranks: Sequence[int] = (1, 5, 10),
    cuhk03_protocol: bool = False,
    repeats: int = 10,
    seed: int = 0,
) -> ReidReport:
    q_pids_a, g_pids_a = np.asarray(q_pids), np.asarray(g_pids)
    q_cams, g_cams = np.asarray(q_camids), np.asarray(g_camids)
    num_q, num_g = distmat.shape
    max_rank = max(ranks)
    if num_g < max_rank:
        max_rank = num_g
    indices = np.argsort(distmat, axis=1)
    matches = (g_pids_a[indices] == q_pids_a[:, None]).astype(np.int32)
    all_cmc, all_ap = [], []
    rng = np.random.default_rng(seed)
    skipped = 0
    for qi in range(num_q):
        order = indices[qi]
        remove = (g_pids_a[order] == q_pids_a[qi]) & (g_cams[order] == q_cams[qi])
        remove |= g_pids_a[order] == -1
        keep = ~remove
        raw = matches[qi][keep]
        if not raw.any():
            skipped += 1
            continue
        if cuhk03_protocol:
            kept_g_pids = g_pids_a[order][keep]
            kept_g_cams = g_cams[order][keep]
            cmc_accum = np.zeros(len(raw))
            for _ in range(repeats):
                mask = np.zeros(len(raw), dtype=bool)
                for cam in np.unique(kept_g_cams[kept_g_pids == q_pids_a[qi]]):
                    candidates = np.where((kept_g_pids == q_pids_a[qi]) & (kept_g_cams == cam))[0]
                    mask[rng.choice(candidates)] = True
                mask |= kept_g_pids != q_pids_a[qi]
                masked = raw[mask]
                cmc = np.cumsum(masked)
                cmc[cmc > 1] = 1
                cmc_accum[: len(cmc)] += cmc
            cmc_curve = cmc_accum / repeats
        else:
            cmc_curve = np.cumsum(raw)
            cmc_curve[cmc_curve > 1] = 1
        all_cmc.append(cmc_curve[:max_rank] if len(cmc_curve) >= max_rank else np.pad(cmc_curve, (0, max_rank - len(cmc_curve)), constant_values=cmc_curve[-1]))
        num_rel = raw.sum()
        tmp = np.cumsum(raw) / np.arange(1, len(raw) + 1)
        all_ap.append(float((tmp * raw).sum() / num_rel))
    if not all_cmc:
        raise ValueError("no query had a valid cross-camera match in the gallery")
    cmc_mean = np.mean(np.stack(all_cmc), axis=0)
    return ReidReport({k: float(cmc_mean[k - 1]) for k in ranks if k <= max_rank}, float(np.mean(all_ap)), len(all_ap), skipped)


@dataclass
class BandCalibration:
    likely_threshold: float
    uncertain_threshold: float
    likely_precision: float
    uncertain_precision: float
    positive_pairs: int
    negative_pairs: int
    positive_similarity_mean: float
    negative_similarity_mean: float

    def as_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vars(self).items()}


def pair_similarities(q_feats: np.ndarray, g_feats: np.ndarray, q_pids: Sequence[int], g_pids: Sequence[int], q_camids: Sequence[int], g_camids: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    """Cosine similarities of every cross-camera query/gallery pair and whether the pair is a true
    match. Same-camera pairs are excluded, as in the runtime, which never matches within one camera."""
    sims = 1.0 - cosine_distance(q_feats, g_feats)
    same_pid = np.asarray(q_pids)[:, None] == np.asarray(g_pids)[None, :]
    cross_camera = np.asarray(q_camids)[:, None] != np.asarray(g_camids)[None, :]
    valid = cross_camera & (np.asarray(g_pids)[None, :] != -1)
    return sims[valid], same_pid[valid]


def calibrate_bands(similarities: np.ndarray, is_match: np.ndarray, *, likely_precision: float = 0.95, uncertain_precision: float = 0.5) -> BandCalibration:
    """Lowest thresholds at which precision (matches among pairs above the threshold) reaches the
    targets. Precision is monotone enough in practice; the scan goes from high to low similarity."""
    if not 0 < uncertain_precision <= likely_precision <= 1:
        raise ValueError("0 < uncertain_precision <= likely_precision <= 1")
    order = np.argsort(-similarities)
    sims_sorted, match_sorted = similarities[order], is_match[order].astype(np.float64)
    cumulative_precision = np.cumsum(match_sorted) / np.arange(1, len(match_sorted) + 1)

    def lowest_threshold(target: float) -> tuple[float, float]:
        ok = np.where(cumulative_precision >= target)[0]
        if ok.size == 0:
            return 1.0, 0.0
        # The longest prefix whose precision stays at or above the target.
        last = ok[0]
        for index in ok:
            if index > last + 1:
                break
            last = index
        return float(sims_sorted[last]), float(cumulative_precision[last])

    likely, likely_p = lowest_threshold(likely_precision)
    uncertain, uncertain_p = lowest_threshold(uncertain_precision)
    uncertain = min(uncertain, likely)
    positives, negatives = similarities[is_match], similarities[~is_match]
    return BandCalibration(
        likely_threshold=likely,
        uncertain_threshold=uncertain,
        likely_precision=likely_p,
        uncertain_precision=uncertain_p,
        positive_pairs=int(is_match.sum()),
        negative_pairs=int((~is_match).sum()),
        positive_similarity_mean=float(positives.mean()) if positives.size else float("nan"),
        negative_similarity_mean=float(negatives.mean()) if negatives.size else float("nan"),
    )
