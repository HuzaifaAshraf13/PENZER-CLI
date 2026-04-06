import os
import glob
import json
import requests
import psutil
from typing import Optional, Union
from llama_cpp import Llama
from dotenv import load_dotenv

# Suppress verbose logging
import logging
logging.getLogger("llama_cpp").setLevel(logging.WARNING)


class DeviceCapabilities:
    """Detect device capabilities and optimize token settings accordingly."""
    
    @staticmethod
    def get_available_memory_gb() -> float:
        """Get available RAM in GB."""
        return psutil.virtual_memory().available / (1024 ** 3)
    
    @staticmethod
    def get_total_memory_gb() -> float:
        """Get total RAM in GB."""
        return psutil.virtual_memory().total / (1024 ** 3)
    
    @staticmethod
    def get_cpu_count() -> int:
        """Get number of CPU cores."""
        return os.cpu_count() or 4
    
    @staticmethod
    def has_gpu() -> bool:
        """Check if GPU is available (CUDA/Metal/etc)."""
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi'], capture_output=True, timeout=2)
            return result.returncode == 0
        except:
            return False
    
    @staticmethod
    def get_optimal_token_config():
        """Calculate optimal token configuration based on device.
        
        Returns:
            dict with keys: max_tokens, n_batch, n_ctx, n_threads
        """
        total_mem = DeviceCapabilities.get_total_memory_gb()
        avail_mem = DeviceCapabilities.get_available_memory_gb()
        cpus = DeviceCapabilities.get_cpu_count()
        has_gpu = DeviceCapabilities.has_gpu()
        
        # Determine tier based on available memory
        if total_mem >= 32:
            # High-end: lots of RAM
            return {
                'max_tokens': 2048,      # Maximum generation
                'n_batch': 1024,         # Large batches
                'n_ctx': 8192,           # Full context
                'n_threads': min(cpus, 16),
                'tier': 'High-end (32GB+)',
            }
        elif total_mem >= 16:
            # Mid-range: standard RAM
            return {
                'max_tokens': 1536,      # Good generation
                'n_batch': 512,          # Medium batches
                'n_ctx': 4096,           # Standard context
                'n_threads': min(cpus, 12),
                'tier': 'Mid-range (16GB)',
            }
        elif total_mem >= 8:
            # Budget: limited RAM
            return {
                'max_tokens': 1024,      # Moderate generation
                'n_batch': 256,          # Small batches
                'n_ctx': 2048,           # Reduced context
                'n_threads': min(cpus, 8),
                'tier': 'Budget (8GB)',
            }
        else:
            # Minimal: very limited RAM
            return {
                'max_tokens': 512,       # Conservative generation
                'n_batch': 128,          # Tiny batches
                'n_ctx': 1024,           # Minimal context
                'n_threads': min(cpus, 4),
                'tier': 'Minimal (<8GB)',
            }
    
    @staticmethod
    def print_capabilities():
        """Print device capabilities info."""
        total = DeviceCapabilities.get_total_memory_gb()
        avail = DeviceCapabilities.get_available_memory_gb()
        cpus = DeviceCapabilities.get_cpu_count()
        gpu = DeviceCapabilities.has_gpu()
        config = DeviceCapabilities.get_optimal_token_config()
        
        print("\n" + "="*70)
        print("DEVICE CAPABILITIES")
        print("="*70)
        print(f"RAM: {total:.1f}GB total, {avail:.1f}GB available")
        print(f"CPUs: {cpus} cores")
        print(f"GPU: {'Yes (CUDA detected)' if gpu else 'No (CPU only)'}")
        print(f"\nOptimal Configuration: {config['tier']}")
        print(f"  • Max tokens: {config['max_tokens']}")
        print(f"  • Batch size: {config['n_batch']}")
        print(f"  • Context: {config['n_ctx']}")
        print(f"  • Threads: {config['n_threads']}")
        print("="*70 + "\n")


class APIModel:
    """External AI API client wrapper."""
    
    def __init__(self, api_key: str, url: str):
        """Initialize API client with credentials.
        
        Args:
            api_key: API authentication key
            url: API endpoint URL
        """
        self.api_key = api_key
        self.url = url
        self.model_name = "external-api"
        print(f"[LLM] Initialized: External AI API")
    
    def create_chat_completion(self, messages: list, max_tokens: int = 512, 
                              temperature: float = 0.7, top_p: float = 0.95) -> dict:
        """Send request to external API and return completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            
        Returns:
            Dict with structure: {"choices": [{"message": {"content": "..."}}]}
        """
        try:
            # Prepare API URL with key
            api_url = self.url
            if "?" not in api_url:
                api_url += f"?key={self.api_key}"
            
            # Build request payload - Google Generative AI format
            prompt = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}" 
                for msg in messages
            ])
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                    "topP": top_p,
                }
            }
            
            response = requests.post(api_url, json=payload, timeout=30)
            response.raise_for_status()
            
            api_response = response.json()
            
            # Extract text from API response (Google Generative AI format)
            if "candidates" in api_response and api_response["candidates"]:
                content = api_response["candidates"][0]["content"]["parts"][0]["text"]
            elif "text" in api_response:
                # Fallback for other API formats
                content = api_response["text"]
            else:
                content = "No response from API"
            
            # Return in llama-cpp compatible format
            return {
                "choices": [{"message": {"content": content}}]
            }
            
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            print(f"[LLM] API Error: {error_msg}")
            return {"choices": [{"message": {"content": f"API Error: {error_msg[:100]}"}}]}
        except (KeyError, IndexError, ValueError) as e:
            error_msg = f"Failed to parse API response: {str(e)}"
            print(f"[LLM] Parse Error: {error_msg}")
            return {"choices": [{"message": {"content": error_msg[:100]}}]}


class LLM:
    """LLM wrapper supporting both local GGUF and external API."""
    
    def __init__(self, model_choice: Optional[str] = None, verbose: bool = False):
        """Initialize LLM with selected backend and device-optimized settings.
        
        Args:
            model_choice: 'local' or 'api'. If None, auto-detects or prompts user.
            verbose: If True, print device capabilities on init
        """
        # Get device-optimized config
        self.device_config = DeviceCapabilities.get_optimal_token_config()
        if verbose:
            DeviceCapabilities.print_capabilities()
        
        if model_choice is None:
            model_choice = self._detect_and_select_model()
        
        self.model_choice = model_choice
        self.model = self._initialize_model(model_choice)
        self.model_name = getattr(self.model, 'model_name', 'unknown-model')
        
        # Store max tokens for this device
        self.max_tokens = self.device_config['max_tokens']
        
        # Test model if it's GGUF (runs actual inference to verify setup)
        if self.model_choice == "local":
            self.test_model()
    
    @staticmethod
    def _check_gguf_available() -> bool:
        """Check if GGUF model exists in model/ directory."""
        gguf_models = glob.glob("model/*.gguf")
        return len(gguf_models) > 0
    
    @staticmethod
    def _check_api_credentials_available() -> bool:
        """Check if .env file contains API credentials."""
        env_path = ".env"
        if not os.path.exists(env_path):
            return False
        
        load_dotenv(env_path, override=False)
        api_key = os.getenv("API_KEY")
        api_url = os.getenv("URL")
        
        return api_key is not None and api_url is not None
    
    @staticmethod
    def _detect_and_select_model() -> str:
        """Detect available models and prompt user if both exist.
        
        Returns:
            'local' or 'api'
            
        Raises:
            FileNotFoundError: If no model source is available
        """
        gguf_available = LLM._check_gguf_available()
        api_available = LLM._check_api_credentials_available()
        
        if not gguf_available and not api_available:
            raise FileNotFoundError(
                "❌ No model source available.\n"
                "   Please provide either:\n"
                "   - A GGUF model file in model/ directory\n"
                "   - A .env file with API_KEY and URL"
            )
        
        if gguf_available and api_available:
            # Both available - prompt user
            print("\n" + "="*60)
            print("Detected both local GGUF model and API credentials.")
            print("Choose which to use:")
            print("  1. Local GGUF model")
            print("  2. AI API")
            print("="*60)
            
            while True:
                try:
                    choice = input("Enter choice [1/2]: ").strip()
                    if choice == "1":
                        return "local"
                    elif choice == "2":
                        return "api"
                    else:
                        print("❌ Invalid choice. Please enter 1 or 2.")
                except (EOFError, KeyboardInterrupt):
                    # Non-interactive or cancelled - default to local
                    print("[AUTO] Defaulting to local GGUF model.")
                    return "local"
                except KeyboardInterrupt:
                    raise RuntimeError("Model selection cancelled by user")
        
        if gguf_available:
            print("[LLM] Auto-detected: Local GGUF model available")
            return "local"
        
        if api_available:
            print("[LLM] Auto-detected: API credentials available")
            return "api"
    
    @staticmethod
    def _load_gguf_model(device_config: dict = None) -> Llama:
        """Load the largest GGUF model from model/ directory with device-optimized settings.
        
        Args:
            device_config: Device configuration dict from DeviceCapabilities
            
        Returns:
            Initialized Llama model instance with proper token handling
            
        Raises:
            FileNotFoundError: If no GGUF models found
        """
        if device_config is None:
            device_config = DeviceCapabilities.get_optimal_token_config()
        
        gguf_models = glob.glob("model/*.gguf")
        if not gguf_models:
            raise FileNotFoundError("No GGUF models found in model/")
        
        # Use largest model
        model_path = sorted(gguf_models, key=os.path.getsize, reverse=True)[0]
        model_name = os.path.basename(model_path)
        model_size_mb = os.path.getsize(model_path) / (1024 * 1024)
        print(f"[LLM] Loading: {model_name} ({model_size_mb:.1f} MB)")
        
        try:
            # Load with device-optimized llama.cpp settings
            model = Llama(
                model_path=model_path,
                n_ctx=device_config['n_ctx'],           # Device-optimized context
                n_batch=device_config['n_batch'],       # Device-optimized batch size
                n_threads=device_config['n_threads'],   # Device-optimized threads
                n_gpu_layers=-1,                        # Auto GPU if available
                f16_kv=True,                           # Memory-efficient KV cache
                use_mlock=True,                        # RAM-lock for speed
                use_mmap=True,                         # Memory mapping
                echo=False,                            # Don't echo prompt
                verbose=False,                         # Suppress verbose logging
                last_n_tokens_size=64,                 # Token history
            )
            
            model.model_name = model_name
            model.model_size_mb = model_size_mb
            model.device_config = device_config
            
            # Log configuration
            print(f"[LLM] ✓ Model loaded successfully")
            print(f"[LLM] ✓ Configuration: {device_config['tier']}")
            print(f"[LLM] ✓ Context: {device_config['n_ctx']} tokens")
            print(f"[LLM] ✓ Max generation: {device_config['max_tokens']} tokens")
            print(f"[LLM] ✓ Batch size: {device_config['n_batch']} tokens")
            print(f"[LLM] ✓ CPU threads: {device_config['n_threads']}")
            
            return model
            
        except Exception as e:
            print(f"[LLM] ✗ Error loading GGUF model: {e}")
            raise
    
    @staticmethod
    def _load_api_model() -> APIModel:
        """Load API client from .env credentials.
        
        Returns:
            Initialized APIModel instance
            
        Raises:
            ValueError: If credentials are missing
        """
        load_dotenv(".env", override=False)
        api_key = os.getenv("API_KEY")
        api_url = os.getenv("URL")
        
        if not api_key or not api_url:
            raise ValueError(
                "Missing API credentials in .env file.\n"
                "Required: API_KEY and URL"
            )
        
        # Remove quotes if present (common in .env files)
        api_key = api_key.strip('"\'')
        api_url = api_url.strip('"\'')
        
        return APIModel(api_key, api_url)
    
    def _initialize_model(self, model_choice: str) -> Union[Llama, APIModel]:
        """Initialize the selected model.
        
        Args:
            model_choice: 'local' or 'api'
            
        Returns:
            Initialized model instance
        """
        if model_choice == "local":
            return self._load_gguf_model(self.device_config)
        elif model_choice == "api":
            return self._load_api_model()
        else:
            raise ValueError(f"Unknown model choice: {model_choice}")
    
    def generate_content(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Generate JSON response from the model with proper token handling.
        
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
            # Determine max tokens based on model type and device
            if isinstance(self.model, Llama):
                # GGUF model - use device-optimized token limit
                max_tokens = self.max_tokens  # Device-optimized
                temperature = 0.5  # Lower temp for consistency
                top_p = 0.9        # Tighter nucleus sampling
            else:
                # API model - use reasonable defaults
                max_tokens = 512
                temperature = 0.7
                top_p = 0.95
            
            # Generate response with full token budget
            response = self.model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            
            output = response["choices"][0]["message"]["content"].strip()
            
            # Extract JSON from markdown code blocks if present
            if "```json" in output:
                # Extract content between ```json and ```
                start = output.find("```json") + 7
                end = output.find("```", start)
                if end > start:
                    output = output[start:end].strip()
            elif "```" in output:
                # Extract content between ``` markers (generic)
                start = output.find("```") + 3
                # Skip language specifier if present (e.g., ```json)
                if start < len(output) and output[start] not in ['\n', '\r']:
                    start = output.find("\n", start) + 1
                end = output.find("```", start)
                if end > start:
                    output = output[start:end].strip()
            
            # Ensure output is valid JSON
            try:
                json.loads(output)
                return output
            except json.JSONDecodeError:
                # Wrap raw output in JSON
                return json.dumps({"thought": output})
                
        except Exception as e:
            print(f"[LLM] Error during generation: {e}")
            return json.dumps({"thought": f"Error: {str(e)[:100]}"})
    
    def test_model(self) -> bool:
        """Test if model is working properly with token generation.
        
        Returns:
            True if model is working, False otherwise
        """
        try:
            if isinstance(self.model, Llama):
                print(f"[LLM] Testing GGUF model: {self.model.model_name}")
                
                # Test with simple prompt
                test_prompt = "What is 2+2? Answer with a single number."
                response = self.model.create_chat_completion(
                    messages=[{"role": "user", "content": test_prompt}],
                    max_tokens=50,
                    temperature=0.3,
                )
                
                output = response["choices"][0]["message"]["content"].strip()
                print(f"[LLM] ✓ Model test passed. Sample output: {output[:50]}...")
                return True
            else:
                print(f"[LLM] Using API model (no test needed)")
                return True
                
        except Exception as e:
            print(f"[LLM] ✗ Model test failed: {e}")
            return False


def get_model_choice() -> LLM:
    """Initialize and return the selected LLM model instance.
    
    This is the main interface function for getting a model. It:
    1. Checks for both .env (API credentials) and GGUF model availability
    2. If both exist, prompts user to choose
    3. If only one exists, automatically selects it
    4. Raises error if neither is found
    
    Returns:
        Initialized LLM instance with selected backend
        
    Raises:
        FileNotFoundError: If no model source is available
        RuntimeError: If user cancels selection
        
    Example:
        >>> llm = get_model_choice()
        >>> response = llm.generate_content("What is the capital of France?")
    """
    return LLM()
