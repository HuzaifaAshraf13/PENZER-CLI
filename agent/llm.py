import os
import glob
import logging
from dotenv import load_dotenv
from typing import Optional
import psutil
import json

try:
    from llama_cpp import Llama
except ImportError:
    raise ImportError("llama_cpp not installed. Run `pip install llama-cpp-python`.")

# Optional: NVIDIA GPU memory check
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError:
    GPU_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class LLM:
    def __init__(self):
        load_dotenv()
        self.model = self._load_model()

    def _select_model_path(self, model_dir="model/"):
        gguf_models = glob.glob(os.path.join(model_dir, "*.gguf"))
        if not gguf_models:
            raise FileNotFoundError(f"No GGUF models found in {model_dir}")
        # Pick largest model (assume bigger model = more capable)
        gguf_models.sort(key=os.path.getsize, reverse=True)
        logging.info(f"Selected model: {gguf_models[0]}")
        return gguf_models[0]

    def _calculate_max_tokens(
        self,
        model_path: str,
        reserved_gb: float = 0.5,
        ram_per_token_gb: float = 0.00006,
        token_cap: int = 8192,
    ) -> int:
        """Estimate how many tokens can fit in the available RAM.

        Args:
            model_path: the path to the model file (used to detect quantization).
            reserved_gb: amount of RAM to keep free for the system.
            ram_per_token_gb: baseline GB per token for an unquantized model.
            token_cap: hard limit on returned token count.
        """

        available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
        usable_ram_gb = max(available_ram_gb - reserved_gb, 0.5)

        # Calculate max tokens based on usable RAM and per-token RAM usage
        max_tokens = int(usable_ram_gb / ram_per_token_gb)

        # Apply safe caps based on usable RAM size
        if usable_ram_gb < 1.0:
            max_tokens = min(max_tokens, 1024)
        elif usable_ram_gb < 2.0:
            max_tokens = min(max_tokens, 2048)
        elif usable_ram_gb < 4.0:
            max_tokens = min(max_tokens, 4096)

        # Ensure token_cap parameter limits the returned tokens
        max_tokens = min(max_tokens, token_cap)

        # Update logging to show actual usable RAM, calculated max tokens, and reserved RAM
        logging.info(
            f"Available RAM: {available_ram_gb:.2f} GB, usable: {usable_ram_gb:.2f} GB, "
            f"reserved: {reserved_gb:.2f} GB, max_tokens: {max_tokens}"
        )
        return max_tokens

    def _load_model(self):
        model_path = self._select_model_path()
        max_tokens = self._calculate_max_tokens(model_path)

        logging.info(f"Selected model path: {model_path}, n_ctx (target): {max_tokens}")
        logging.info(f"GPU available: {GPU_AVAILABLE}")
        n_gpu_layers = -1 if GPU_AVAILABLE else 0

        # Heuristic adjustments for low-RAM systems and q4_k_m models
        fname = os.path.basename(model_path).lower()
        use_mmap = True
        use_mlock = False if psutil.virtual_memory().available / (1024 ** 3) < 1.0 else True
        kv_cache = True
        low_vram = True
        n_threads = min(4, (os.cpu_count() or 1))

        try:
            llm = Llama(
                model_path=model_path,
                n_ctx=max_tokens,
                n_gpu_layers=n_gpu_layers,
                n_threads=n_threads,
                use_mmap=use_mmap,
                use_mlock=use_mlock,
                kv_cache=kv_cache,
                low_vram=low_vram,
                verbose=False,
            )
        except Exception as e:
            logging.warning(f"Model load failed ({e}), falling back to safer CPU settings...")
            llm = Llama(
                model_path=model_path,
                n_ctx=max_tokens,
                n_gpu_layers=0,
                n_threads=n_threads,
                use_mmap=use_mmap,
                use_mlock=False,
                kv_cache=kv_cache,
                low_vram=True,
                verbose=False,
            )

        return llm

    def generate_content(self, prompt: str, system_instruction: Optional[str] = None, stream: bool = False):
        messages = []
        if system_instruction:
            system_content = system_instruction + "\nYour output MUST be a JSON object with a 'thought' key."
        else:
            system_content = "Your output MUST be a JSON object with a 'thought' key."

        messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": prompt})

        # Calculate prompt tokens (a rough estimate). If tokenization fails,
        # fall back to a characters->tokens heuristic (4 chars per token).
        try:
            prompt_token_ids = self.model.tokenize(str(messages).encode("utf-8"))
            prompt_tokens = len(prompt_token_ids)
        except Exception:
            prompt_tokens = max(1, len(prompt) // 4)

        # Leave some room for the prompt and a buffer
        max_ctx = self.model.n_ctx()

        # Ensure there's always room for a small response; if the prompt is too
        # long, truncate the user message (best-effort via character truncation).
        min_response_tokens = 32
        buffer_tokens = 20
        allowed_prompt_tokens = max_ctx - min_response_tokens - buffer_tokens

        if allowed_prompt_tokens <= 0:
            # Context too small to generate; return a helpful error as JSON string
            err = {"thought": f"Model context window ({max_ctx}) too small to generate a response."}
            return json.dumps(err)

        if prompt_tokens > allowed_prompt_tokens:
            # Truncate user prompt to fit
            ratio = allowed_prompt_tokens / max(1, prompt_tokens)
            approx_chars = int(len(prompt) * ratio)
            truncated = prompt[-approx_chars:] if approx_chars > 0 else ""
            logging.warning(f"Prompt too long ({prompt_tokens} tokens); truncating to approx {allowed_prompt_tokens} tokens.")
            messages[-1]["content"] = truncated
            try:
                prompt_token_ids = self.model.tokenize(str(messages).encode("utf-8"))
                prompt_tokens = len(prompt_token_ids)
            except Exception:
                prompt_tokens = max(1, len(truncated) // 4)

        max_response_tokens = max(min(max_ctx - prompt_tokens - buffer_tokens, 512), min_response_tokens)
        logging.info(f"Context window: {max_ctx}, prompt tokens: {prompt_tokens}, response tokens cap: {max_response_tokens}")

        try:
            response = self.model.create_chat_completion(
                messages=messages,
                max_tokens=max_response_tokens,
                stop=["<|im_end|>"],
                temperature=0.7,
            )

            raw_output = response["choices"][0]["message"]["content"]
            try:
                # Attempt to parse the output as JSON; return a JSON string to
                # match the agent's expectation (double-quoted JSON).
                parsed_output = json.loads(raw_output)
                return json.dumps(parsed_output)
            except json.JSONDecodeError:
                # If parsing fails, wrap the raw output as the 'thought' key and
                # return a JSON string so the agent receives valid JSON.
                logging.warning(f"LLM did not return valid JSON. Wrapping raw output.")
                return json.dumps({"thought": raw_output})
        except Exception as e:
            logging.error(f"Error during generation: {e}")
            # Return a JSON string so the agent can parse it correctly
            return json.dumps({"thought": f"⚠️ Model failed to generate content: {e}"})