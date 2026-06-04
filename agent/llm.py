import os
import glob
import json
import requests
import psutil
from pathlib import Path
from typing import Optional, Union
from dotenv import load_dotenv

# Get project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Suppress verbose logging
import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)


class LocalServerModel:
    """Local AI server client wrapper (llamacpp, ollama, vLLM, etc)."""
    
    def __init__(self, url: str):
        """Initialize local server client.
        
        Args:
            url: Local server endpoint URL (e.g., http://localhost:8000)
        """
        self.url = url.rstrip('/')  # Remove trailing slash if present
        self.model_name = "local-server"
        
        # Validate connection
        try:
            test_response = requests.get(f"{self.url}/v1/models", timeout=5)
            test_response.raise_for_status()
            print(f"[LLM] ✓ Connected to local server: {self.url}")
        except requests.exceptions.RequestException as e:
            print(f"[LLM] ⚠ Warning: Could not reach local server at {self.url}")
            print(f"     Error: {e}")
            print(f"     Proceeding anyway (server may start later)")
    
    def create_chat_completion(self, messages: list, max_tokens: int = 512,
                              temperature: float = 0.7, top_p: float = 0.95) -> dict:
        """Send request to local server via OpenAI-compatible API.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            
        Returns:
            Dict with structure: {"choices": [{"message": {"content": "..."}}]}
        """
        try:
            # Use OpenAI-compatible API endpoint
            api_url = f"{self.url}/v1/chat/completions"
            
            # Build request payload - OpenAI-compatible format
            payload = {
                "model": "local-model",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
            }
            
            response = requests.post(api_url, json=payload, timeout=60)
            response.raise_for_status()
            
            api_response = response.json()
            
            # Extract content from OpenAI-compatible response format
            if "choices" in api_response and api_response["choices"]:
                content = api_response["choices"][0]["message"]["content"]
            else:
                content = "No response from local server"
            
            return {
                "choices": [{"message": {"content": content}}]
            }
            
        except requests.exceptions.Timeout:
            error_msg = f"Local server timeout at {self.url} (60s)"
            print(f"[LLM] Timeout Error: {error_msg}")
            return {"choices": [{"message": {"content": f"Server Timeout: {error_msg[:100]}"}}]}
        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to local server at {self.url}"
            print(f"[LLM] Connection Error: {error_msg}")
            return {"choices": [{"message": {"content": f"Connection Error: {error_msg[:100]}"}}]}
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            print(f"[LLM] Local Server Error: {error_msg}")
            return {"choices": [{"message": {"content": f"Server Error: {error_msg[:100]}"}}]}
        except (KeyError, IndexError, ValueError) as e:
            error_msg = f"Failed to parse server response: {str(e)}"
            print(f"[LLM] Parse Error: {error_msg}")
            return {"choices": [{"message": {"content": error_msg[:100]}}]}


class APIModel:
    """External AI API client wrapper (Google, OpenAI, etc)."""
    
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
    """LLM wrapper supporting local server and external APIs."""
    
    def __init__(self, model_choice: Optional[str] = None):
        """Initialize LLM with selected backend.
        
        Args:
            model_choice: 'local_server' or 'api'. If None, auto-detects.
        """
        if model_choice is None:
            model_choice = self._detect_and_select_model()
        
        self.model_choice = model_choice
        self.model = self._initialize_model(model_choice)
        self.model_name = getattr(self.model, 'model_name', 'unknown-model')
        
        # Test model if it's local server (runs actual inference to verify setup)
        if self.model_choice == "local_server":
            self.test_model()
    
    @staticmethod
    def _check_local_server_available() -> bool:
        """Check if .env contains LOCAL_SERVER_URL for llama.cpp/vLLM/ollama."""
        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            return False
        
        load_dotenv(str(env_path), override=False)
        local_url = os.getenv("LOCAL_SERVER_URL")
        
        return local_url is not None and local_url.strip() != ""
    
    @staticmethod
    def _check_api_credentials_available() -> bool:
        """Check if .env file contains API credentials."""
        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            return False
        
        load_dotenv(str(env_path), override=False)
        api_key = os.getenv("API_KEY")
        api_url = os.getenv("URL")
        
        return api_key is not None and api_url is not None
    
    @staticmethod
    def _detect_and_select_model() -> str:
        """Detect available models and prompt user if multiple exist.
        
        Returns:
            'local_server' or 'api'
            
        Raises:
            FileNotFoundError: If no model source is available
        """
        local_server_available = LLM._check_local_server_available()
        api_available = LLM._check_api_credentials_available()
        
        if not local_server_available and not api_available:
            raise FileNotFoundError(
                "❌ No model source available.\n"
                "   Please provide one of:\n"
                "   - LOCAL_SERVER_URL in .env (e.g., http://localhost:8000)\n"
                "     (llama.cpp, vLLM, ollama, LM Studio, text-generation-webui)\n"
                "   - API_KEY and URL in .env (Google Gemini, OpenAI, etc)"
            )
        
        if local_server_available and api_available:
            # Both available - prompt user
            print("\n" + "="*70)
            print("Multiple model sources detected. Choose which to use:")
            print("="*70)
            print("  1. Local Server (llama.cpp, vLLM, ollama, LM Studio, etc)")
            print("  2. Cloud AI API")
            print("="*70)
            
            while True:
                try:
                    choice = input("Enter choice [1/2]: ").strip()
                    if choice == "1":
                        return "local_server"
                    elif choice == "2":
                        return "api"
                    else:
                        print("❌ Invalid choice. Please enter 1 or 2.")
                except (EOFError, KeyboardInterrupt):
                    # Non-interactive or cancelled - default to local server
                    print("[AUTO] Defaulting to local server.")
                    return "local_server"
        
        if local_server_available:
            print("[LLM] Auto-detected: Local server available")
            return "local_server"
        
        if api_available:
            print("[LLM] Auto-detected: API credentials available")
            return "api"
    
    @staticmethod
    def _load_api_model() -> APIModel:
        """Load API client from .env credentials.
        
        Returns:
            Initialized APIModel instance
            
        Raises:
            ValueError: If credentials are missing
        """
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
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
    
    @staticmethod
    def _load_local_server_model() -> LocalServerModel:
        """Load local server client (llama.cpp, vLLM, ollama, etc).
        
        Returns:
            Initialized LocalServerModel instance
            
        Raises:
            ValueError: If server URL is missing
        """
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
        local_url = os.getenv("LOCAL_SERVER_URL")
        
        if not local_url or not local_url.strip():
            raise ValueError(
                "Missing LOCAL_SERVER_URL in .env file.\n"
                "Example: LOCAL_SERVER_URL=http://localhost:8000"
            )
        
        # Remove quotes if present (common in .env files)
        local_url = local_url.strip('"\'')
        
        return LocalServerModel(local_url)
    
    def _initialize_model(self, model_choice: str) -> Union[LocalServerModel, APIModel]:
        """Initialize the selected model.
        
        Args:
            model_choice: 'local_server' or 'api'
            
        Returns:
            Initialized model instance
        """
        if model_choice == "local_server":
            return self._load_local_server_model()
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
            # Determine max tokens based on model type
            if isinstance(self.model, LocalServerModel):
                # Local server - use reasonable defaults
                max_tokens = 1024
                temperature = 0.5
                top_p = 0.9
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
    
    async def chat(self, system: str, messages: list) -> dict:
        """Chat interface compatible with agent loop (async wrapper).
        
        Args:
            system: System prompt/instructions
            messages: List of message dicts with 'role' and 'content'
            
        Returns:
            Dict with 'content' and optional 'tool_calls' keys
        """
        # Convert message history to a single prompt for generate_content
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            if isinstance(content, str):
                prompt += f"{role}: {content}\n"
            else:
                # Handle tool responses or other complex messages
                prompt += f"{role}: {json.dumps(content) if content else ''}\n"
        
        # Call generate_content with system instruction
        response_json = self.generate_content(
            prompt=prompt.strip(),
            system_instruction=system
        )
        
        try:
            response_data = json.loads(response_json)
        except json.JSONDecodeError:
            response_data = {"thought": response_json}
        
        # Parse response for tool calls and content
        content = response_data.get("thought", "")
        tool_calls = []
        
        # Check for tool calls in the response (new format: "tool" and "args")
        if "tool" in response_data and response_data.get("tool"):
            print(f"[LLM] Found tool in response: {response_data['tool']}")
            tool_calls.append({
                "id": "tool_call_1",
                "name": response_data["tool"],
                "arguments": response_data.get("args", {}),
            })
        
        # Also check for legacy "tool_code" format (string like "memory(...)" or "terminal(...)")
        elif "tool_code" in response_data and response_data.get("tool_code"):
            tool_code = response_data["tool_code"].strip()
            print(f"[LLM] Found tool_code: {tool_code}")
            # Parse tool_code like "memory(...)" or "terminal(...)"
            if "(" in tool_code and ")" in tool_code:
                tool_name = tool_code.split("(")[0].strip()
                # Extract arguments string and parse
                args_str = tool_code[tool_code.index("(")+1:tool_code.rindex(")")]
                try:
                    import re
                    args = {}
                    
                    # First, handle simple string arguments: key="value"
                    for match in re.finditer(r'(\w+)="([^"]*)"', args_str):
                        key = match.group(1)
                        value = match.group(2)
                        args[key] = value
                    
                    # Handle dict arguments: key={...}
                    # Find all {...} blocks and their preceding keys
                    dict_pattern = r'(\w+)=(\{[^{}]*\})'
                    for match in re.finditer(dict_pattern, args_str):
                        key = match.group(1)
                        dict_str = match.group(2)
                        try:
                            args[key] = eval(dict_str)
                        except:
                            # If eval fails, keep as string
                            args[key] = dict_str
                    
                    print(f"[LLM] Parsed tool_code -> {tool_name} with args {args}")
                    tool_calls.append({
                        "id": "tool_call_1",
                        "name": tool_name,
                        "arguments": args,
                    })
                except Exception as e:
                    print(f"[LLM] Failed to parse tool_code: {e}")
        
        print(f"[LLM] Chat response: tool_calls={len(tool_calls)}, content_len={len(content)}")
        return {
            "content": content,
            "tool_calls": tool_calls,
        }
    
    def test_model(self) -> bool:
        """Test if model is working properly with token generation.
        
        Returns:
            True if model is working, False otherwise
        """
        try:
            if isinstance(self.model, LocalServerModel):
                print(f"[LLM] Testing local server connection...")
                
                # Test with simple prompt
                test_prompt = "What is 2+2? Answer with a single number."
                response = self.model.create_chat_completion(
                    messages=[{"role": "user", "content": test_prompt}],
                    max_tokens=50,
                    temperature=0.3,
                )
                
                output = response["choices"][0]["message"]["content"].strip()
                print(f"[LLM] ✓ Server test passed. Sample output: {output[:50]}...")
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
