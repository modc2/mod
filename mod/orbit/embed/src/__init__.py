"""embed — small models, made smaller, with the cost written down.

    onnxfile   an .onnx file read and written from raw protobuf
    runtime    those graphs run with nothing but numpy
    quantize   float32 → float16 / int8 / int4, and the error each one costs
    compress   the same, applied to a whole model file, still valid ONNX
    zoo        the two models this module builds on the spot
    text       words → integers, by hashing
    data       the corpora, small enough to read
    evaluate   what compression did to the answers, not just to the weights
    check      the same model through onnxruntime, when it is installed
"""
