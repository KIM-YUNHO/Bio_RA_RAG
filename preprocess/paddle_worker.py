"""One dedicated Paddle process. Exit before Qwen so GPU memory is released."""
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import traceback
import time


def read_chart_labels(pipeline, result, image_path, settings):
    """Reuse the document VLM's OCR task; never invoke chart digitization."""
    import cv2
    import math
    image = None
    references = {}
    for index, block in enumerate(result.get("res", result).get("parsing_res_list", [])):
        if block.get("block_label") != "chart":
            continue
        try:
            if image is None:
                image = cv2.imread(image_path)
            x0, y0, x1, y1 = block["block_bbox"]
            box = [max(0, math.floor(x0)), max(0, math.floor(y0)),
                   min(image.shape[1], math.ceil(x1)), min(image.shape[0], math.ceil(y1))]
            crop = image[box[1]:box[3], box[0]:box[2]].copy()
            rows = list(pipeline.predict(input=crop, use_layout_detection=False, prompt_label="ocr",
                use_chart_recognition=False, use_queues=False, temperature=0,
                max_new_tokens=settings["max_new_tokens"]))
            if len(rows) != 1:
                raise ValueError("Chart label OCR must return exactly one crop result")
            raw = rows[0].json
            text = "\n".join(b["block_content"] for b in raw.get("res", raw)["parsing_res_list"])
            references[str(index)] = {"task": "ocr", "text": text, "crop_box_input_px": box, "raw": raw}
        except Exception as exc:
            references[str(index)] = {"task": "ocr", "text": "", "error": f"{type(exc).__name__}: {exc}"}
    result["ra_figure_ocr"] = references


def forbid_external_connections():
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple):
            host = address[0]
            if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
                raise RuntimeError("External connections are disabled during preprocessing")
        return original(sock, address)
    socket.socket.connect = connect


def main():
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"):
        os.environ[name] = "1" if name != "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK" else "True"
    forbid_external_connections()
    settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # SDK logs belong on stderr; stdout is a small JSON-line protocol.
    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCRVL
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6", engine="transformers", device=settings["device"],
            layout_detection_model_name="PP-DocLayoutV3", layout_detection_model_dir=settings["layout_model_dir"],
            vl_rec_model_name="PaddleOCR-VL-1.6-0.9B", vl_rec_model_dir=settings["vl_model_dir"],
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_layout_detection=True,
            use_chart_recognition=settings["chart_recognition"], use_ocr_for_image_block=settings["image_ocr"],
            markdown_ignore_labels=[], format_block_content=False, use_queues=False,
        )
        core=getattr(pipeline.paddlex_pipeline,'_pipeline',pipeline.paddlex_pipeline)
        core.vl_rec_model.batch_sampler.batch_size=settings.get('vl_rec_batch_size',1)
        native_recognizer=None
        if settings.get('native_text'):
            from .native_text import NativeRecognizer
            native_recognizer=NativeRecognizer(pipeline)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                import torch
                if torch.cuda.is_available():
                    torch.cuda.reset_peak_memory_stats()
                started = time.perf_counter()
                label_only = request.get('task') == 'ocr'
                native=request.get('native') if not label_only else None
                if native_recognizer:native_recognizer.begin(native)
                options = {'use_layout_detection': False, 'prompt_label': 'ocr', 'use_chart_recognition': False} if label_only else {}
                results = list(pipeline.predict(input=request["image"], temperature=0,
                    max_new_tokens=settings["max_new_tokens"], use_queues=False, **options))
                if len(results) != 1:
                    raise ValueError("A page image must produce exactly one result")
                result = results[0].json
                native_count=native_recognizer.native_count if native is not None else 0
                vlm_count=native_recognizer.vlm_count if native is not None else None
                if native is not None:
                    from .native_text import project
                    result=project(result,native)
                    result['ra_native_text'].update(native_inference_skipped=native_count,vlm_blocks=vlm_count)
                if native_recognizer:native_recognizer.begin(None)
                if not label_only and settings["image_ocr"] and not settings["chart_recognition"]:
                    read_chart_labels(pipeline, result, request["image"], settings)
                result["ra_runtime"] = {"inference_seconds": round(time.perf_counter() - started, 3),
                    "vl_rec_batch_size":core.vl_rec_model.batch_sampler.batch_size,
                    "native_blocks":native_count,
                    "vlm_blocks":vlm_count,
                    "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
                    "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved() if torch.cuda.is_available() else None}
            print(json.dumps({"result": result}, ensure_ascii=False, allow_nan=False), flush=True)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)


if __name__ == "__main__":
    main()
