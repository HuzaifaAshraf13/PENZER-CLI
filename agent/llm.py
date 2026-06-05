"""
Minimal LLM interface - supports local servers and cloud APIs
"""
import os
import json
import requests
from pathlib import Path
from typing import Optional, Union
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent


class LocalServerModel:
    """Local AI server (llama.cpp, ollama, vLLM, etc)"""
    
    def __init__(self, url: str):
        self.url = url.rstrip('/')
        self.model_name = "local-server"
    
    def create_chat_completion(self, messages: list, max_tokens: int = 1024,
                              temperature: float = 0.5, top_p: float = 0.9) -> dict:
        """Call local OpenAI-compatible endpoint"""
        try:
            response = requests.post(
                f"{self.url}/v1/chat/completions",
                json={
                    "model": "local-model",
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                },
                timeout=60
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return {"choices": [{"message": {"content": content}}]}
        except Exception as e:
            return {"choices": [{"message": {"content": f"Error: {str(e)[:100]}"}}]}


class APIModel:
    """Cloud AI API (Google Gemini, OpenAI, etc)"""
    
    def __init__(self, api_key: str, url: str):
        self.api_key = api_key
        self.url = url
        self.model_name = "api"
    
    def create_chat_completion(self, messages: list, max_tokens: int = 512,
                              temperature: float = 0.7, top_p: float = 0.95) -> dict:
        """Call cloud API endpoint"""
        try:
            prompt = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
            response = requests.post(
                self.url if "?" in self.url else f"{self.url}?key={self.api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": temperature,
                        "topP": top_p,
                    }
                },
                timeout=30
            )
            response.raise_for_status()
            api_resp = response.json()
            content = api_resp["candidates"][0]["content"]["parts"][0]["text"]
            return {"choices": [{"message": {"content": content}}]}
        except Exception as e:
            return {"choices": [{"message": {"content": f"Error: {str(e)[:100]}"}}]}


class LLM:
    """Main LLM wrapper"""
    
    def __init__(self):
        self.model = self._init_model()
        self.model_name = getattr(self.model, 'model_name', 'unknown')
    
    def _init_model(self) -> Union[LocalServerModel, APIModel]:
        """Initialize local server or API based on .env"""
        load_dotenv(str(PROJECT_ROOT / ".env"), override=False)
        
        local_url = os.getenv("LOCAL_SERVER_URL", "").strip().strip('"\'')
        api_key = os.getenv("API_KEY", "").strip().strip('"\'')
        api_url = os.getenv("URL", "").strip().strip('"\'')
        
        if local_url:
            print("[LLM] Auto-detected: Local server available")
            return LocalServerModel(local_url)
        elif api_key and api_url:
            print("[LLM] Auto-detected: API credentials available")
            return APIModel(api_key, api_url)
        else:
            raise FileNotFoundError("No LOCAL_SERVER_URL or API credentials in .env")
    
    def generate_content(self, prompt: str, system: Optional[str] = None) -> str:
        """Generate JSON response"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        response = self.model.create_chat_completion(messages)
        output = response["choices"][0]["message"]["content"].strip()
        
        # Extract JSON from code blocks if present
        if "```json" in output:
            start = output.find("```json") + 7
            end = output.find("```", start)
            output = output[start:end].strip() if end > start else output
        elif "```" in output:
            start = output.find("```") + 3
            if start < len(output) and output[start] not in ['\n', '\r']:
                start = output.find("\n", start) + 1
            end = output.find("```", start)
            output = output[start:end].strip() if end > start else output
        
        # Ensure valid JSON
        try:
            json.loads(output)
            return output
        except json.JSONDecodeError:
            return json.dumps({"thought": output})
    
    async def chat(self, system: str, messages: list) -> dict:
        """Chat interface for agent"""
        # Build prompt from message history
        prompt = "\n".join([
            f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
            for msg in messages
        ])
        
        response_json = self.generate_content(prompt, system)
        
        try:
            response_data = json.loads(response_json)
        except json.JSONDecodeError:
            response_data = {"thought": response_json}
        
        # Parse tool calls
        content = response_data.get("thought", "")
        tool_calls = []
        
        if "tool" in response_data and response_data.get("tool"):
            tool_name = response_data["tool"].strip()
            tool_args = response_data.get("args", {})
            tool_calls.append({
                "id": "tool_call_1",
                "name": tool_name,
                "arguments": tool_args if isinstance(tool_args, dict) else {},
            })
        
        return {"content": content, "tool_calls": tool_calls}


def get_model_choice() -> LLM:
    """Get LLM instance"""
    return LLM()
