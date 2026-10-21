"""Read the deployed Qwen GGUF vocabulary without loading model tensors.

GGUF layout: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
Qwen35 pretokenizer: https://github.com/ggml-org/llama.cpp/blob/master/src/llama-vocab.cpp
"""
from functools import lru_cache
import json
from pathlib import Path
import struct

PATTERN = r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?[\p{L}\p{M}]+|\p{N}| ?[^\s\p{L}\p{M}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"


def metadata(path):
    formats = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
    with Path(path).open('rb') as f:
        def number(fmt):
            size = struct.calcsize('<'+fmt)
            raw = f.read(size)
            if len(raw) != size: raise ValueError('Incomplete GGUF metadata')
            return struct.unpack('<'+fmt, raw)[0]
        def string():
            size = number('Q')
            if size > 32*1024*1024: raise ValueError('GGUF string too large')
            return f.read(size).decode('utf-8')
        def value(kind):
            if kind == 8: return string()
            if kind == 9:
                subtype, count = number('I'), number('Q')
                if count > 1000000: raise ValueError('GGUF metadata array too large')
                return [value(subtype) for _ in range(count)]
            if kind not in formats: raise ValueError('Unknown GGUF metadata type')
            return number(formats[kind])
        if f.read(4) != b'GGUF' or number('I') != 3: raise ValueError('Expected GGUF v3')
        number('Q')  # tensor count; tensor contents are never read
        count = number('Q')
        result = {}
        for _ in range(count):
            key = string(); result[key] = value(number('I'))
        return result


class LocalTokenizer:
    def __init__(self, model='qwen3.5:9b', model_root=None):
        from tokenizers import Tokenizer, Regex, AddedToken, models, pre_tokenizers
        root = Path(model_root or Path.home()/'.ollama/models')
        name, tag = model.rsplit(':', 1)
        if '/' in name or '\\' in name or tag in ('.','..') or '/' in tag or '\\' in tag:
            raise ValueError('Only installed library model names are supported')
        manifest = json.loads((root/'manifests/registry.ollama.ai/library'/name/tag).read_text())
        layer = next(x for x in manifest['layers'] if x['mediaType'] == 'application/vnd.ollama.image.model')
        self.model_digest = layer['digest']
        data = metadata(root/'blobs'/layer['digest'].replace(':','-'))
        if data.get('tokenizer.ggml.pre') != 'qwen35' or data.get('tokenizer.ggml.model') != 'gpt2':
            raise ValueError('Unsupported deployed tokenizer; do not estimate a different vocabulary')
        tokens = data['tokenizer.ggml.tokens']
        merges = [tuple(x.split(' ',1)) for x in data['tokenizer.ggml.merges']]
        self.tokenizer = Tokenizer(models.BPE(vocab={v:i for i,v in enumerate(tokens)}, merges=merges))
        self.tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
            pre_tokenizers.Split(Regex(PATTERN), behavior='isolated'),
            pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
        self.tokenizer.add_special_tokens([AddedToken(t, normalized=False, special=True)
            for t, kind in zip(tokens,data['tokenizer.ggml.token_type']) if kind in (3,4)])
        self.template = data.get('tokenizer.chat_template')
        self.basis = 'deployed_gguf_qwen35_bpe'

    @lru_cache(maxsize=8192)
    def one(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def messages(self, messages):
        # Conservative framing allowance is explicit; content uses actual model BPE.
        # Measured Ollama prompt_eval_count remains the authoritative runtime total.
        return sum(self.one(m['content'])+self.one(m['role'])+4 for m in messages)+32


@lru_cache(maxsize=2)
def for_model(model='qwen3.5:9b'): return LocalTokenizer(model)
