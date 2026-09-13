"""One isolated MLX model process per pipeline phase (no HTTP server needed)."""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

from capture2doc.config import VllmWorkerSettings


class MlxRuntime:
    """Keep Metal allocations out of the durable worker; join before switching."""

    def __init__(self, settings: VllmWorkerSettings, model_path: Path, log_path: Path):
        self.settings = settings
        self.model_path = model_path
        self.log_path = log_path
        self.process: Any = None
        self.connection: Any = None
        self.metrics: dict[str, Any] = {}
        self.ready = False
        self.busy = False

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("MLX worker is already started")
        if not self.model_path.is_dir():
            raise RuntimeError(f"Model snapshot is not a directory: {self.model_path}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=_serve, args=(child, self.settings, self.model_path, self.log_path),
            name=f"c2d-{self.settings.served_model_name}",
        )
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            self.connection = None
            self.process = None
            raise
        finally:
            child.close()

    def _receive(self, timeout: float) -> dict[str, Any]:
        if not self.connection.poll(timeout):
            raise TimeoutError(f"MLX worker exceeded {timeout:g}s; log: {self.log_path}")
        try:
            result = self.connection.recv()
        except (EOFError, OSError) as exc:
            raise RuntimeError(f"MLX worker exited unexpectedly; log: {self.log_path}") from exc
        if "error" in result:
            raise RuntimeError(f"MLX: {result['error']}; log: {self.log_path}")
        self.metrics.update(result.pop("metrics", {}))
        return result

    def wait_ready(self) -> None:
        self._receive(self.settings.startup_timeout_seconds)
        self.ready = True

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.process is None or not self.process.is_alive():
            raise RuntimeError("MLX phase is not running")
        if self.busy:
            raise RuntimeError("Previous MLX request did not complete; restart the phase")
        self.busy = True
        self.connection.send(request)
        result = self._receive(1200)
        self.busy = False
        return result

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.is_alive() and self.ready and not self.busy:
                try:
                    self.connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                self.process.join(self.settings.shutdown_timeout_seconds)
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(5)
            if self.process.is_alive():
                raise RuntimeError("MLX worker did not exit; next model was not started")
        finally:
            if self.connection is not None:
                self.connection.close()


def _serve(connection, settings, model_path, log_path) -> None:
    # Imports and Metal state belong exclusively to this spawned child.
    import traceback

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    with open(log_path, "a", buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            model, processor, metadata = _load(settings, model_path)
            connection.send({"metrics": metadata})
            while True:
                request = connection.recv()
                if request is None:
                    break
                connection.send(_generate(model, processor, settings, request))
        except EOFError:
            pass
        except BaseException as exc:
            traceback.print_exc()
            try:
                connection.send({"error": f"{type(exc).__name__}: {exc}"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        finally:
            connection.close()


def quantization_profile(name):
    if name is None:
        return None
    if name == "mlx8bit":
        return {"mode": "affine", "bits": 8, "group_size": 64}
    if name == "mxfp8":
        return {"mode": "mxfp8", "bits": 8, "group_size": 32}
    raise ValueError(f"Unsupported MLX quantization {name!r}; use mlx8bit or mxfp8, not vLLM fp8_per_channel")


def quantize_weights(model, config, profile):
    """Use MLX-VLM conversion rules, preserving vision and model-specific exclusions."""
    from mlx_vlm.quant_utils import quantize_model
    from mlx_vlm.utils import skip_multimodal_module

    predicate = getattr(model, "quant_predicate", None)

    def select(path, module):
        if skip_multimodal_module(path):
            return False
        return predicate(path, module) if predicate is not None else True

    return quantize_model(model, config, quant_predicate=select, **profile)


def _load(settings, model_path):
    import gc
    import json

    import mlx.core as mx
    from mlx.utils import tree_flatten, tree_map_with_path
    from mlx_vlm import load

    config = json.loads((model_path / "config.json").read_text())
    profile = quantization_profile(settings.quantization)
    stored = config.get("quantization") or config.get("quantization_config")
    if stored and (profile is None or any(stored.get(k, "affine" if k == "mode" else None) != v
                                        for k, v in profile.items())):
        raise ValueError("Checkpoint quantization does not match the selected MLX profile")
    mx.set_cache_limit(256 * 1024**2)
    model, processor = load(str(model_path), lazy=True,
                            trust_remote_code=settings.trust_remote_code)
    dtype = getattr(mx, settings.dtype)
    predicate = getattr(model, "cast_predicate", lambda _: True)
    if not stored:
        model.update(tree_map_with_path(
            lambda key, value: value.astype(dtype)
            if predicate(key) and mx.issubdtype(value.dtype, mx.floating) else value,
            model.parameters(),
        ))
        if profile is not None:
            model, config = quantize_weights(model, config, profile)
    weights = tree_flatten(model.parameters())
    weight_bytes = sum(value.nbytes for _, value in weights)
    recommended = mx.device_info().get("max_recommended_working_set_size")
    if recommended and weight_bytes >= recommended:
        raise RuntimeError("Model weights exceed the recommended Metal working set; use --qwen-model 4b")
    dtypes: dict[str, int] = {}
    for key, value in weights:
        dtypes[str(value.dtype)] = dtypes.get(str(value.dtype), 0) + value.nbytes
        if predicate(key) and mx.issubdtype(value.dtype, mx.floating) and value.dtype != dtype:
            raise RuntimeError(f"Unexpected weight dtype for {key}: {value.dtype}")
    mx.eval(model.parameters())
    del weights
    gc.collect()
    mx.clear_cache()
    configure_image_processor(processor, settings, model_path)
    return model, processor, {
        "dtype": settings.dtype, "quantization": settings.quantization,
        "quantization_config": config.get("quantization") or stored,
        "quantization_source": "checkpoint" if stored else "on_load" if profile else None,
        "weight_bytes": weight_bytes, "weight_bytes_by_dtype": dtypes,
        "active_memory_bytes": mx.get_active_memory(),
        "peak_memory_bytes": mx.get_peak_memory(),
    }


def configure_image_processor(processor, settings, model_path: Path) -> None:
    """MLX uses min/max_pixels attributes; HF preflight uses size edges."""
    import json

    config = json.loads((model_path / "preprocessor_config.json").read_text())
    minimum = config.get("size", {}).get("shortest_edge", config.get("min_pixels"))
    image_processor = processor.image_processor
    image_processor.max_pixels = settings.max_pixels
    if minimum is not None:
        image_processor.min_pixels = minimum
    if hasattr(image_processor, "size"):
        size = {**image_processor.size, "longest_edge": settings.max_pixels}
        if minimum is not None:
            size["shortest_edge"] = minimum
        image_processor.size = size


def _generate(model, processor, settings, request):
    import gc

    import mlx.core as mx
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.structured import build_json_schema_logits_processor
    from mlx_vlm.utils import prepare_inputs

    prompt = request.get("rendered_prompt")
    if prompt is None:
        prompt = apply_chat_template(processor, model.config, "OCR:", num_images=1)
    inputs = prepare_inputs(
        processor, images=[request["image"]], prompts=prompt,
        image_token_index=getattr(model.config, "image_token_index", None),
        add_special_tokens=False,
    )
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    expected = request.get("prompt_tokens")
    if expected is not None and prompt_tokens != expected:
        raise ValueError(f"MLX prompt token count {prompt_tokens} differs from preflight {expected}")
    output = request["max_tokens"]
    if output <= 0 or output > settings.max_output_tokens or prompt_tokens + output > settings.max_model_len:
        raise ValueError("MLX request exceeds the configured context/output budget")
    if "attention_mask" in inputs:
        inputs["mask"] = inputs.pop("attention_mask")
    processors = []
    if request.get("response_schema") is not None:
        processors.append(build_json_schema_logits_processor(
            processor.tokenizer, request["response_schema"],
        ))
    started = time.monotonic()
    result = generate(
        model, processor, prompt, max_tokens=output, temperature=0.0,
        enable_thinking=False, prefill_step_size=256, verbose=False,
        logits_processors=processors, **inputs,
    )
    raw = {
        "model": settings.served_model_name,
        "choices": [{"message": {"role": "assistant", "content": result.text},
                     "finish_reason": result.finish_reason}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": result.generation_tokens,
                  "total_tokens": prompt_tokens + result.generation_tokens},
    }
    del inputs
    gc.collect()
    mx.clear_cache()
    return {"content": result.text, "raw_response": raw, "metrics": {
        "active_memory_bytes": mx.get_active_memory(),
        "peak_memory_bytes": mx.get_peak_memory(),
        "last_generation_seconds": time.monotonic() - started,
    }}
