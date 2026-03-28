import os
import glob
import json
import requests
from typing import Optional, Union
from llama_cpp import Llama
from dotenv import load_dotenv

# Suppress verbose logging
import logging
logging.getLogger("llama_cpp").setLevel(logging.WARNING)


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
    
    def __init__(self, model_choice: Optional[str] = None):
        """Initialize LLM with selected backend.
        
        Args:
            model_choice: 'local' or 'api'. If None, auto-detects or prompts user.
        """
        if model_choice is None:
            model_choice = self._detect_and_select_model()
        
        self.model_choice = model_choice
        self.model = self._initialize_model(model_choice)
        self.model_name = getattr(self.model, 'model_name', 'unknown-model')
    
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
    def _load_gguf_model() -> Llama:
        """Load the largest GGUF model from model/ directory.
        
        Returns:
            Initialized Llama model instance
            
        Raises:
            FileNotFoundError: If no GGUF models found
        """
        gguf_models = glob.glob("model/*.gguf")
        if not gguf_models:
            raise FileNotFoundError("No GGUF models found in model/")
        
        # Use largest model
        model_path = sorted(gguf_models, key=os.path.getsize, reverse=True)[0]
        model_name = os.path.basename(model_path)
        print(f"[LLM] Loading: {model_name}")
        
        # Load with sensible defaults
        model = Llama(
            model_path=model_path,
            n_ctx=4096,              # Context window
            n_gpu_layers=0,          # CPU inference (change to -1 if GPU available)
            n_threads=4,             # CPU threads
            verbose=False,
        )
        model.model_name = model_name
        return model
    
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
            return self._load_gguf_model()
        elif model_choice == "api":
            return self._load_api_model()
        else:
            raise ValueError(f"Unknown model choice: {model_choice}")
    
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
                if output[start] not in ['\n', '\r']:
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
            print(f"[LLM] Error: {e}")
            return json.dumps({"thought": f"Error: {str(e)[:100]}"})


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
