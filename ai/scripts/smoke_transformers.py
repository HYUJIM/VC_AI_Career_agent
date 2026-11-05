"""One real CUDA response; connectivity check, not a research evaluation.

Test target: Python 3.11, torch 2.5.1+cu118, transformers 4.51.3,
safetensors 0.5.3. Uses FP32/eager on CUDA, no Accelerate or server.
Reference: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
from time import perf_counter
from uuid import uuid4

MODEL_ID = 'Qwen/Qwen2.5-1.5B-Instruct'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', default='main', help='HF commit or revision; resolved commit is logged')
    parser.add_argument('--max-new-tokens', type=int, default=128)
    args = parser.parse_args()
    if not 1 <= args.max_new_tokens <= 256:
        parser.error('--max-new-tokens must be 1..256 for this short smoke test')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    output = Path(__file__).resolve().parents[1] / 'outputs' / 'llm_smoke' / str(uuid4())
    output.mkdir(parents=True, exist_ok=False)
    result = {'purpose': 'real_cuda_connectivity_smoke_only', 'status': 'running',
              'model_id': MODEL_ID, 'requested_revision': args.revision,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'python': platform.python_version(), 'platform': platform.platform(),
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'inference_performed': False, 'dtype': 'float32', 'attention': 'eager',
              'quantization': 'none', 'seed': 42, 'do_sample': False,
              'max_new_tokens': args.max_new_tokens}
    def save():
        (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    save()
    try:
        import torch
        import transformers
        import safetensors
        from transformers import AutoConfig, AutoTokenizer, AutoModelForCausalLM, GenerationConfig

        result.update(torch_version=torch.__version__, transformers_version=transformers.__version__,
                      safetensors_version=safetensors.__version__, torch_cuda_runtime=torch.version.cuda)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable. CPU fallback is intentionally disabled.')
        device = torch.device('cuda:0')
        result.update(gpu=torch.cuda.get_device_name(0), compute_capability=list(torch.cuda.get_device_capability(0)),
                      gpu_total_bytes=torch.cuda.get_device_properties(0).total_memory)
        print('GPU:', result['gpu'], flush=True)
        print('Loading Qwen 1.5B (first run downloads model files)...', flush=True)
        torch.manual_seed(42)
        torch.cuda.manual_seed_all(42)
        started = perf_counter()
        config = AutoConfig.from_pretrained(MODEL_ID, revision=args.revision, trust_remote_code=False)
        revision = getattr(config, '_commit_hash', None)
        if not revision:
            raise RuntimeError('Unable to resolve model commit for reproducible loading.')
        result['resolved_revision'] = revision
        save()
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=revision, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, revision=revision, config=config,
            torch_dtype=torch.float32, attn_implementation='eager',
            use_safetensors=True, trust_remote_code=False,
            low_cpu_mem_usage=False,
        )
        model.to(device).eval()
        torch.cuda.synchronize()
        result['load_seconds_including_download'] = perf_counter() - started
        result['parameter_device'] = str(next(model.parameters()).device)
        result['parameter_dtype'] = str(next(model.parameters()).dtype)
        print('Model loaded on', result['parameter_device'], result['parameter_dtype'], flush=True)
        messages = [
            {'role': 'system', 'content': '제공된 근거만 사용해 한국어로 짧게 답하세요. 자료에 없으면 확인할 수 없다고 답하세요. 사실 주장 뒤에 근거 ID를 인용하세요.'},
            {'role': 'user', 'content': '근거 [record-001]: 가상인물 가는 Python 기초 교육을 30시간 이수했습니다.\n질문: 가상인물 가의 Python 기초 교육 이수 시간은 몇 시간인가요?'}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors='pt', add_special_tokens=False).to(device)
        input_tokens = inputs['input_ids'].shape[-1]
        if input_tokens > 1024:
            raise ValueError('Unexpectedly long smoke prompt')
        generation = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=args.max_new_tokens,
                                      eos_token_id=model.generation_config.eos_token_id,
                                      pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id,
                                      use_cache=True)
        result.update(messages=messages, rendered_prompt=text, input_tokens=input_tokens,
                      generation_config=generation.to_dict())
        save()
        print('Generating a real response...', flush=True)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = perf_counter()
        with torch.inference_mode():
            outputs = model.generate(**inputs, generation_config=generation)
        torch.cuda.synchronize()
        seconds = perf_counter()-started
        new_ids = outputs[0, input_tokens:]
        answer = tokenizer.decode(new_ids, skip_special_tokens=True)
        result.update(status='completed', inference_performed=True, generated_answer=answer,
                      output_tokens=int(new_ids.numel()), generation_seconds=seconds,
                      peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                      peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                      ended_at=datetime.now(timezone.utc).isoformat())
        save()
        print('\nANSWER:\n'+answer, flush=True)
        print(f'Generation: {seconds:.2f}s; output tokens: {new_ids.numel()}', flush=True)
        print('Saved:', output/'result.json', flush=True)
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__)
        save()
        print('Failed. Partial metadata saved:', output/'result.json', file=sys.stderr)
        raise

if __name__ == '__main__':
    main()
