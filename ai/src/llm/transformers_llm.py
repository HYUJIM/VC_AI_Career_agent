"""Pinned CUDA-only development backend matching the successful user smoke run."""
import re
from time import perf_counter

MODEL_ID = 'Qwen/Qwen2.5-1.5B-Instruct'
REVISION = '989aa7980e4cf806f80c7fef2b1adb7bc71aa306'

def validate_settings(revision, max_new_tokens, max_input_tokens):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact 40-character model commit is required')
    if not 1 <= max_new_tokens <= 256 or not 1 <= max_input_tokens <= 2048:
        raise ValueError('Development bounds: output 1..256, input 1..2048 tokens')

class TransformersLLM:
    def __init__(self, revision=REVISION, max_new_tokens=128, max_input_tokens=2048,
                 seed=42, local_files_only=True):
        validate_settings(revision, max_new_tokens, max_input_tokens)
        import torch
        import transformers
        import safetensors
        from transformers import AutoConfig, AutoTokenizer, AutoModelForCausalLM, GenerationConfig
        self.torch = torch
        self.last_generation = None
        self.max_input_tokens = max_input_tokens
        self.seed = seed
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is required; no automatic CPU fallback')
        self.device = torch.device('cuda:0')
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        started = perf_counter()
        options = dict(revision=revision, trust_remote_code=False, local_files_only=local_files_only)
        config = AutoConfig.from_pretrained(MODEL_ID, **options)
        if getattr(config, '_commit_hash', None) != revision:
            raise ValueError('Resolved model revision does not match pinned revision')
        if getattr(config, 'use_sliding_window', False):
            raise ValueError('This eager development backend requires use_sliding_window=false')
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, **options)
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, config=config, torch_dtype=torch.float32,
            attn_implementation='eager', use_safetensors=True, low_cpu_mem_usage=False, **options)
        self.model.to(self.device).eval()
        torch.cuda.synchronize()
        self.generation_config = GenerationConfig(
            do_sample=False, num_beams=1, max_new_tokens=max_new_tokens, use_cache=True,
            eos_token_id=self.model.generation_config.eos_token_id,
            pad_token_id=self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else self.tokenizer.eos_token_id)
        self.runtime_metadata = {
            'backend': 'transformers-direct-cuda-v1', 'model_id': MODEL_ID, 'resolved_revision': revision,
            'torch': torch.__version__, 'transformers': transformers.__version__,
            'safetensors': safetensors.__version__, 'cuda_runtime': torch.version.cuda,
            'gpu': torch.cuda.get_device_name(0), 'gpu_total_bytes': torch.cuda.get_device_properties(0).total_memory,
            'compute_capability': list(torch.cuda.get_device_capability(0)),
            'dtype': str(next(self.model.parameters()).dtype), 'device': str(next(self.model.parameters()).device),
            'attention': 'eager', 'use_sliding_window': config.use_sliding_window,
            'sliding_window': getattr(config, 'sliding_window', None),
            'quantization': 'none', 'seed': seed, 'local_files_only': local_files_only,
            'max_input_tokens': max_input_tokens, 'load_seconds': perf_counter()-started,
            'generation_config': self.generation_config.to_dict(),
            'message_policy': 'pipeline prompt in single user message; tokenizer chat template applied',
        }

    def generate(self, prompt):
        self.last_generation = None
        torch = self.torch
        # Each condition is an independent conversation, with no preceding answer.
        messages = [{'role': 'user', 'content': prompt}]
        rendered = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer([rendered], return_tensors='pt', add_special_tokens=False)
        n = inputs['input_ids'].shape[-1]
        if n > self.max_input_tokens:
            raise ValueError(f'Input has {n} tokens; limit is {self.max_input_tokens}. No truncation performed.')
        inputs = inputs.to(self.device)
        torch.manual_seed(self.seed)
        torch.cuda.manual_seed_all(self.seed)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = perf_counter()
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, generation_config=self.generation_config)
        torch.cuda.synchronize()
        seconds = perf_counter()-started
        ids = outputs[0, n:]
        answer = self.tokenizer.decode(ids, skip_special_tokens=True)
        eos = self.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        ended_with_eos = bool(ids.numel()) and int(ids[-1]) in eos
        self.last_generation = {
            'messages': messages, 'rendered_prompt': rendered,
            'token_usage': {'input_tokens': n, 'output_tokens': int(ids.numel()), 'total_tokens': n+int(ids.numel())},
            'generation_seconds_cuda_synchronized': seconds,
            'finish_reason': 'eos' if ended_with_eos else 'length' if ids.numel() >= self.generation_config.max_new_tokens else 'other',
            'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
            'peak_reserved_bytes': torch.cuda.max_memory_reserved(),
        }
        return answer
