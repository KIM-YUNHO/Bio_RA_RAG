"""PaddleOCR-VL-1.6 full-pipeline adapter; no remote model service or auto-download."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

from .ingest import sha256, stable_hash


def model_fingerprint(path):
    path = Path(path)
    if not path.is_dir() or not (path / "config.json").is_file():
        raise FileNotFoundError(f"Local Transformers model is missing: {path}")
    files = [p for p in sorted(path.rglob("*")) if p.is_file() and ".cache" not in p.parts
             and p.suffix in (".json", ".safetensors", ".bin", ".model", ".yml", ".yaml", ".py", ".jinja", ".txt")]
    if not any(p.suffix in (".safetensors", ".bin") for p in files):
        raise FileNotFoundError(f"Model weights missing: {path}")
    manifest = [[p.relative_to(path).as_posix(), sha256(p)] for p in files]
    return {"sha256": stable_hash(manifest), "files": manifest}


class PaddleAdapter:
    def __init__(self, settings, out):
        self.settings, self.out = settings, Path(out)
        self.process = None
        self.log = None

    def fingerprint(self):
        if not Path(self.settings.python).is_file():
            raise FileNotFoundError(f"Paddle runtime is missing: {self.settings.python}")
        runtime = subprocess.run([self.settings.python, "-B", "-c",
            "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ['paddleocr','paddlex','transformers','torch','numpy']}))"],
            capture_output=True, text=True, timeout=30, check=True)
        settings=asdict(self.settings)
        if self.settings.parser_backend=='cascade':
            root=Path(__file__).resolve().parents[1]
            return {'name':'PP-DocLayoutV3 + PP-OCR mobile','pipeline':'region-cascade-1',
                'settings':settings,'runtime':json.loads(runtime.stdout),
                'layout':model_fingerprint(self.settings.layout_model_dir),
                'detection':model_fingerprint(self.settings.light_det_model_dir),
                'recognition':model_fingerprint(self.settings.light_rec_model_dir),
                'code':{n:sha256(root/'preprocess'/n) for n in ('cascade_worker.py','light_ocr.py',
                    'simple_grid.py','visual_router.py','native_text.py','native_tables.py','native_page.py')}}
        for field in ('parser_backend','light_det_model_dir','light_rec_model_dir'):settings.pop(field)
        settings.pop('native_text');batch=settings.pop('vl_rec_batch_size')
        result = {"name": "PaddleOCR-VL-1.6", "pipeline": "v1.6", "settings": settings,
                "runtime": json.loads(runtime.stdout), "layout": model_fingerprint(self.settings.layout_model_dir),
                "recognition": model_fingerprint(self.settings.vl_model_dir)}
        if batch!=1:result['settings']['vl_rec_batch_size']=batch
        if self.settings.native_text:
            from .native_text import VERSION
            root=Path(__file__).resolve().parents[1]
            result['native_text']={'version':VERSION,'code_sha256':{
                name:sha256(root/name) for name in ('preprocess/native_text.py','preprocess/native_tables.py','preprocess/native_fonts.py','preprocess/native_page.py','preprocess/paddle_worker.py','text_layer.py')}}
        return result

    def _receive(self):
        try:
            item = self.messages.get(timeout=self.settings.timeout_s)
        except queue.Empty:
            self.close()
            raise TimeoutError("Paddle worker timed out; successful page checkpoints are retained")
        if item is None:
            raise RuntimeError(f"Paddle worker exited; inspect {self.out / 'paddle_worker.log'}")
        return item

    def _start(self):
        self.out.mkdir(parents=True, exist_ok=True)
        settings_path = self.out / "paddle_worker_settings.json"
        settings_path.write_text(json.dumps(asdict(self.settings)), encoding="utf-8")
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.log = (self.out / "paddle_worker.log").open("a", encoding="utf-8")
        module='preprocess.cascade_worker' if self.settings.parser_backend=='cascade' else 'preprocess.paddle_worker'
        self.process = subprocess.Popen([self.settings.python, "-B", "-m", module, str(settings_path)],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.messages = queue.Queue()
        process, messages = self.process, self.messages

        def read():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    messages.put({"error": "Invalid Paddle worker protocol"})
            messages.put(None)
        threading.Thread(target=read, daemon=True).start()
        if self._receive() != {"ready": True}:
            self.close()
            raise RuntimeError("Paddle worker initialization failed")

    def parse(self, image):
        return self._predict(image, 'page')

    def parse_page(self, image, page):
        if not self.settings.native_text and self.settings.parser_backend!='cascade':return self.parse(image)
        from PIL import Image
        from .native_text import packet
        with Image.open(image) as raster:size=raster.size
        return self._predict(image,'page',native=packet(page,size))

    def ocr(self, image):
        return self._predict(image, 'ocr')

    def _predict(self, image, task, native=None):
        if self.process is None:
            self._start()
        self.process.stdin.write(json.dumps({"image": str(image), "task": task,"native":native},ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        reply = self._receive()
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["result"]

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
            self.process.stdout.close()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None
