"""Local-only configuration for the Paddle parser and selective Qwen enrichment."""
from dataclasses import asdict, dataclass, field
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


def local_endpoint(value):
    url = urlsplit(value)
    if url.scheme != "http" or url.username or url.password or url.query or url.fragment:
        raise ValueError("Ollama must use an unauthenticated local HTTP endpoint")
    host = url.hostname
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host or "").is_loopback:
                raise ValueError("Ollama endpoint must be loopback")
        except ValueError as exc:
            raise ValueError("Ollama endpoint must be localhost or a loopback IP") from exc
    if url.path not in ("", "/"):
        raise ValueError("Specify the Ollama base URL, without an API path")
    return value.rstrip("/")


@dataclass
class PaddleSettings:
    python: str = sys.executable
    layout_model_dir: str = "models/paddle/PP-DocLayoutV3_safetensors"
    vl_model_dir: str = "models/paddle/PaddleOCR-VL-1.6"
    device: str = "gpu:0"
    dpi: int = 150
    max_new_tokens: int = 4096
    timeout_s: int = 600
    chart_recognition: bool = False
    image_ocr: bool = True
    release_qwen_before_parser: bool = True
    native_text: bool = False
    vl_rec_batch_size: int = 1
    parser_backend: str = 'cascade'
    light_det_model_dir: str = 'models/paddle/PP-OCRv5_mobile_det_safetensors'
    light_rec_model_dir: str = 'models/paddle/latin_PP-OCRv5_mobile_rec_safetensors'


@dataclass
class QwenSettings:
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:9b"
    timeout_s: int = 600
    num_ctx: int = 8192
    num_predict: int = 3000
    crop_padding_px: int = 12
    crop_dpi: int = 300
    max_image_edge: int = 1800
    keep_alive: str = "5m"


@dataclass
class HybridConfig:
    paddle: PaddleSettings = field(default_factory=PaddleSettings)
    qwen: QwenSettings = field(default_factory=QwenSettings)
    schema_version: str = "ra-extraction-1"

    def to_dict(self):
        return asdict(self)

    def validate(self):
        local_endpoint(self.qwen.endpoint)
        for name, value, lower, upper in (
            ("dpi", self.paddle.dpi, 72, 400),
            ("parser tokens", self.paddle.max_new_tokens, 256, 16384),
            ("parser timeout", self.paddle.timeout_s, 1, 3600),
            ("VL recognition batch", self.paddle.vl_rec_batch_size, 1, 16),
            ("Qwen timeout", self.qwen.timeout_s, 1, 3600),
            ("Qwen context", self.qwen.num_ctx, 1024, 65536),
            ("Qwen tokens", self.qwen.num_predict, 256, 16384),
            ("crop padding", self.qwen.crop_padding_px, 0, 200),
            ("crop dpi", self.qwen.crop_dpi, 72, 400),
            ("image edge", self.qwen.max_image_edge, 256, 4096),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"Invalid {name}: {value}")
        if self.schema_version != "ra-extraction-1":
            raise ValueError("Unsupported extraction schema")
        if not self.qwen.model:
            raise ValueError("A local vision model name is required")
        if self.paddle.parser_backend not in {'cascade','paddle_vl'}:
            raise ValueError('Unknown parser backend')
        for name in ("chart_recognition", "image_ocr", "release_qwen_before_parser", "native_text"):
            if type(getattr(self.paddle, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        return self


def load_config(path=None):
    root = Path(__file__).resolve().parents[1]
    data = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    unknown = data.keys() - {"paddle", "qwen", "schema_version"}
    if unknown:
        raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
    config = HybridConfig(PaddleSettings(**data.get("paddle", {})), QwenSettings(**data.get("qwen", {})),
                          data.get("schema_version", "ra-extraction-1"))
    for name in ("python", "layout_model_dir", "vl_model_dir",'light_det_model_dir','light_rec_model_dir'):
        value = Path(getattr(config.paddle, name))
        setattr(config.paddle, name, str((root / value).resolve()))
    return config.validate()


def parse_pages(value):
    """One-based physical pages; None means every page, never a hidden allowlist."""
    if value is None:
        return None
    pages = set()
    for part in value.split(","):
        bounds = part.strip().split("-")
        if len(bounds) > 2 or not all(x.isdigit() for x in bounds):
            raise ValueError("Pages must look like 1,3,5-8")
        start, end = int(bounds[0]), int(bounds[-1])
        if start < 1 or end < start or end - start > 100000:
            raise ValueError("Invalid physical page range")
        pages.update(range(start, end + 1))
    return sorted(pages)
