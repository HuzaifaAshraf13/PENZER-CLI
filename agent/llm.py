import os
import glob
from dotenv import load_dotenv
from typing import Optional
from llama_cpp import Llama
import psutil

class LLM:
    def __init__(self):
        load_dotenv()
        self.model = self._load_model()

    def _load_model(self):
        model_dir = "model/"
        model_path = None
        
        # Search for .gguf models in the model directory
        gguf_models = glob.glob(os.path.join(model_dir, "*.gguf"))
        if not gguf_models:
            raise FileNotFoundError(f"No GGUF models found in the directory: {model_dir}")
        
        # For simplicity, load the first GGUF model found
        model_path = gguf_models[0]
        print(f"Loading model from: {model_path}")

        # Determine max_tokens based on available RAM
        available_ram_gb = psutil.virtual_memory().available / (1024**3)
        # This is a simplified heuristic. Adjust based on actual model memory usage.
        if available_ram_gb > 16:
            max_tokens = 4096
        elif available_ram_gb > 8:
            max_tokens = 2048
        else:
            max_tokens = 1024 # Minimum reasonable token count

        print(f"Available RAM: {available_ram_gb:.2f} GB, setting max_tokens: {max_tokens}")

        llm = Llama(
            model_path=model_path,
            n_ctx=max_tokens,  # Context window
            n_gpu_layers=-1,  # Uncomment to use GPU, -1 for all layers
            verbose=False
        )
        return llm

    def generate_content(self, prompt: str, system_instruction: Optional[str] = None):
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        # Calculate prompt tokens (a rough estimate)
        prompt_tokens = self.model.tokenize(self.model.encode(str(messages)).encode("utf-8"))
        
        # Leave some room for the prompt and a buffer
        max_response_tokens = self.model.n_ctx - len(prompt_tokens) - 50  # 50 for a small buffer
        if max_response_tokens < 100: # Ensure a minimum response length
            max_response_tokens = 100

        response = self.model.create_chat_completion(
            messages=messages,
            max_tokens=max_response_tokens, # Use the calculated max_response_tokens
            stop=["<|im_end|>"], # Adjust stop tokens based on your model
            temperature=0.7,
        )
        return response["choices"][0]["message"]["content"]