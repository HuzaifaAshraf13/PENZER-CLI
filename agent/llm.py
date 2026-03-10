import os
import glob
import json
from typing import Optional
from llama_cpp import Llama

# Suppress verbose logging
import logging
logging.getLogger("llama_cpp").setLevel(logging.WARNING)


class LLM:
    """Simple GGUF model wrapper using llama-cpp-python."""
    
    def __init__(self):
        """Load the GGUF model from model/ directory."""
        self.model = self._load_model()
    
    def _load_model(self) -> Llama:
        """Load the largest GGUF model from model/ directory."""
        # Find GGUF files
        gguf_models = glob.glob("model/*.gguf")
        if not gguf_models:
            raise FileNotFoundError("No GGUF models found in model/")
        
        # Use largest model
        model_path = sorted(gguf_models, key=os.path.getsize, reverse=True)[0]
        print(f"[LLM] Loading: {os.path.basename(model_path)}")
        
        # Load with sensible defaults
        return Llama(
            model_path=model_path,
            n_ctx=4096,              # Context window
            n_gpu_layers=0,          # CPU inference (change to -1 if GPU available)
            n_threads=4,             # CPU threads
            verbose=False,
        )
    
    def generate_content(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Generate JSON response from the model.
        
        Args:
            prompt: User/conversation prompt
            system_instruction: Optional system instructions
            
        Returns:
            JSON string with thought, tool, args, final_answer fields
        """
        # Build messages
        messages = []
        
        # Add system instruction
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        
        # Add user prompt
        messages.append({"role": "user", "content": prompt})
        
        try:
            # Generate response
            response = self.model.create_chat_completion(
                messages=messages,
                max_tokens=512,
                temperature=0.7,
                top_p=0.95,
            )
            
            output = response["choices"][0]["message"]["content"].strip()
            
            # Ensure output is valid JSON
            try:
                json.loads(output)
                return output
            except json.JSONDecodeError:
                # Wrap raw output in JSON
                return json.dumps({"thought": output})
                
        except Exception as e:
            print(f"[LLM] Error: {e}")
            return json.dumps({"thought": f"Error: {str(e)[:100]}"})
