import numpy as np
import pytest
from sklearn.metrics import average_precision_score as sk_ap
from sklearn.metrics import roc_auc_score as sk_auc

from evaluation.evaluate_tracking import MotBox, evaluate_tracking
from evaluation.metrics_classification import average_precision_score, best_threshold, binary_report, roc_auc, video_level_scores
from evaluation.metrics_detection import GroundTruth, Prediction, average_precision, best_threshold_by_f1, box_iou, evaluate_detections
from evaluation.metrics_reid import calibrate_bands, cosine_distance, evaluate_rank, pair_similarities

# --- detection ---------------------------------------------------------------------------------------------------


def test_box_iou_and_average_precision() -> None:
    a = np.array([[0, 0, 10, 10]], dtype=float)
    assert box_iou(a, np.array([[0, 0, 10, 10], [5, 5, 15, 15], [20, 20, 30, 30]], dtype=float))[0].round(4).tolist() == [1.0, 0.1429, 0.0]
    truths = [GroundTruth("i1", "handgun", (0, 0, 10, 10)), GroundTruth("i2", "handgun", (0, 0, 10, 10))]
    perfect = [Prediction("i1", "handgun", 0.9, (0, 0, 10, 10)), Prediction("i2", "handgun", 0.8, (1, 1, 10, 10))]
    assert average_precision(perfect, truths) == pytest.approx(1.0)
    assert average_precision([], truths) == 0.0
    half = [Prediction("i1", "handgun", 0.9, (0, 0, 10, 10)), Prediction("i3", "handgun", 0.95, (0, 0, 10, 10))]
    assert 0.2 < average_precision(half, truths) < 0.6  # one TP ranked below one FP, one truth missed


def test_detection_report_counts_false_positives_on_negative_frames() -> None:
    truths = [GroundTruth("pos", "knife", (0, 0, 10, 10))]
    predictions = [
        Prediction("pos", "knife", 0.9, (0, 0, 10, 10)),
        Prediction("neg1", "knife", 0.7, (0, 0, 5, 5)),  # false alarm above threshold
        Prediction("neg2", "knife", 0.2, (0, 0, 5, 5)),  # below threshold: not an alarm
    ]
    report = evaluate_detections(predictions, truths, class_names=["handgun", "knife"], image_ids=["pos", "neg1", "neg2", "neg3"], score_threshold=0.5)
    assert report.negative_images == 3 and report.false_positive_images == 1
    assert report.false_positives_per_1000_negative_frames == pytest.approx(1000 / 3)
    knife = report.per_class["knife"]
    assert (knife.precision, knife.recall, knife.support) == (0.5, 1.0, 1)
    assert report.per_class["handgun"].support == 0 and report.map50 == pytest.approx(knife.ap50)
    assert best_threshold_by_f1(predictions, truths, label="knife")[0] >= 0.75  # dropping the 0.7 FP maximises F1
    assert set(report.as_dict()) >= {"mAP50", "mAP50_95", "per_class", "false_positives_per_1000_negative_frames"}


# --- classification --------------------------------------------------------------------------------------------------


def test_auc_metrics_match_sklearn_including_ties() -> None:
    rng = np.random.default_rng(0)
    labels = rng.integers(0, 2, 200)
    scores = np.round(rng.random(200), 1)  # many ties
    assert roc_auc(scores, labels) == pytest.approx(sk_auc(labels, scores))
    assert average_precision_score(scores, labels) == pytest.approx(sk_ap(labels, scores))
    assert np.isnan(roc_auc([0.1, 0.2], [1, 1]))


def test_binary_report_threshold_choice_and_video_level() -> None:
    scores, labels = [0.9, 0.8, 0.4, 0.3, 0.6], [1, 1, 0, 0, 0]
    report = binary_report(scores, labels, 0.5)
    assert report.confusion == {"tp": 2, "fp": 1, "fn": 0, "tn": 2} and report.false_alarm_rate == pytest.approx(1 / 3)
    assert report.roc_auc == 1.0 and report.pr_auc == 1.0
    assert best_threshold(scores, labels) > 0.6
    assert best_threshold(scores, labels, max_false_alarm_rate=0.0) > 0.6
    video_scores, video_labels = video_level_scores([0.2, 0.8, 0.5], [0, 0, 1], [0, 0, 1])
    assert video_scores == [0.5, 0.5] and video_labels == [0, 1]


# --- re-id ------------------------------------------------------------------------------------------------------------------


def test_rank_metrics_follow_the_market_protocol() -> None:
    q = np.array([[1.0, 0.0]])
    g = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    distmat = cosine_distance(q, g)
    # gallery 0 is the same person on the same camera: removed; gallery 1 is the true cross-camera match
    report = evaluate_rank(distmat, q_pids=[7], g_pids=[7, 7, 8], q_camids=[1], g_camids=[1, 2, 2], ranks=(1, 5))
    assert report.cmc[1] == 1.0 and report.mAP == 1.0 and report.queries_evaluated == 1
    worse = evaluate_rank(cosine_distance(np.array([[0.0, 1.0]]), g), [8], [7, 7, 8], [1], [1, 2, 2], ranks=(1,))
    assert worse.cmc[1] == 1.0  # gallery 2 is pid 8 on camera 2, the nearest after removing nothing
    with pytest.raises(ValueError):
        evaluate_rank(distmat, [9], [7, 7, 8], [1], [1, 2, 2])
    sims, is_match = pair_similarities(q, g, [7], [7, 7, 8], [1], [1, 2, 2])
    assert len(sims) == 2 and is_match.tolist() == [True, False]  # same-camera pair excluded


def test_band_calibration_finds_precision_thresholds() -> None:
    rng = np.random.default_rng(1)
    positives = rng.normal(0.8, 0.05, 200)
    negatives = rng.normal(0.3, 0.1, 2000)
    sims = np.concatenate([positives, negatives])
    is_match = np.concatenate([np.ones(200, bool), np.zeros(2000, bool)])
    bands = calibrate_bands(sims, is_match, likely_precision=0.95, uncertain_precision=0.5)
    assert bands.likely_threshold >= bands.uncertain_threshold
    assert bands.likely_precision >= 0.95 and bands.uncertain_precision >= 0.5
    assert is_match[sims >= bands.likely_threshold].mean() >= 0.95
    assert bands.positive_similarity_mean > bands.negative_similarity_mean


# --- tracking ----------------------------------------------------------------------------------------------------------------


def test_tracking_metrics_count_switches_misses_and_false_positives() -> None:
    truth = [MotBox(f, 1, (0, 0, 10, 10)) for f in range(1, 5)]
    perfect = [MotBox(f, 9, (0, 0, 10, 10)) for f in range(1, 5)]
    report = evaluate_tracking(truth, perfect)
    assert report.mota == 1.0 and report.motp == 1.0 and report.idf1 == 1.0
    switched = [MotBox(1, 9, (0, 0, 10, 10)), MotBox(2, 9, (0, 0, 10, 10)), MotBox(3, 5, (0, 0, 10, 10)), MotBox(5, 5, (50, 50, 60, 60))]
    report = evaluate_tracking(truth, switched)
    assert (report.id_switches, report.misses, report.false_positives) == (1, 1, 1)
    assert report.mota == pytest.approx(1 - 3 / 4) and report.idf1 < 1.0
