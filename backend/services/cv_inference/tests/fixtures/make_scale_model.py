"""Generates ``scale2.onnx``: a one-node graph that multiplies a float32 [N, 4] input by 2.
It is NOT a vision model; it only lets the tests check that ``OnnxRuntimeAdapter`` loads, selects a
device, runs and times a real ONNX graph without downloading any weights.

Run with an environment that has the ``onnx`` package (``backend/ml_training/.venv``):
    ../../ml_training/.venv/bin/python tests/fixtures/make_scale_model.py
"""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

two = numpy_helper.from_array(np.array([2.0], dtype=np.float32), name="two")
node = helper.make_node("Mul", ["input", "two"], ["output"])
graph = helper.make_graph(
    [node],
    "scale2",
    [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 4])],
    [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", 4])],
    initializer=[two],
)
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], producer_name="cv_inference-tests")
model.ir_version = 8
onnx.checker.check_model(model)
onnx.save(model, Path(__file__).with_name("scale2.onnx"))
