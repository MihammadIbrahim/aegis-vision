"""MJPEG splitting and wall-clock frame sampling."""

from src.frame_sampler import FrameSampler, MjpegSplitter


def jpeg(payload: bytes) -> bytes:
    return b"\xff\xd8" + payload + b"\xff\xd9"


def test_splitter_cuts_frames_across_chunks_and_drops_garbage() -> None:
    splitter = MjpegSplitter()
    stream = b"junk" + jpeg(b"aaa") + jpeg(b"bb") + b"\xff\xd8partial"
    out: list[bytes] = []
    for i in range(0, len(stream), 3):  # feed in tiny pieces
        out.extend(splitter.feed(stream[i : i + 3]))
    assert out == [jpeg(b"aaa"), jpeg(b"bb")]
    out.extend(splitter.feed(b"rest\xff\xd9"))
    assert out[-1] == jpeg(b"partialrest")


def test_splitter_discards_an_oversized_frame() -> None:
    splitter = MjpegSplitter(max_frame_bytes=16)
    assert list(splitter.feed(b"\xff\xd8" + b"x" * 40)) == []
    assert list(splitter.feed(jpeg(b"ok"))) == [jpeg(b"ok")]


def test_sampler_admits_at_most_the_configured_rate() -> None:
    sampler = FrameSampler(5.0)  # every 0.2 s
    admitted = [t for t in [i * 0.05 for i in range(41)] if sampler.admit(t)]  # 25 fps for 2 s
    assert len(admitted) == 11 and admitted[:3] == [0.0, 0.2, 0.4]


def test_sampler_re_anchors_after_a_stall() -> None:
    sampler = FrameSampler(2.0)
    assert sampler.admit(0.0) and not sampler.admit(0.1) and sampler.admit(0.5)
    # A 10 s stall, then a burst: only one frame per interval is admitted, not the backlog.
    burst = [t for t in (10.5, 10.55, 10.6, 10.65, 11.0, 11.05) if sampler.admit(t)]
    assert burst == [10.5, 11.0]
    sampler.reset()
    assert sampler.admit(0.0)
