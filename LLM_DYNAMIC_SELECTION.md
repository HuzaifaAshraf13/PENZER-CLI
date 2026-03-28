# Dynamic LLM Selection with JSON Extraction Fix

## Overview
The `llm.py` module now supports:
1. **Dynamic model selection** - Choose between local GGUF or external API at runtime
2. **Automatic detection** - Intelligently detects available resources
3. **Robust JSON parsing** - Extracts JSON from markdown code blocks in LLM responses

## How It Works

### 1. Model Detection & Selection

The system checks for:
- **Local GGUF**: Files in `model/` directory
- **API Credentials**: `API_KEY` and `URL` in `.env` file

**Logic:**
- If both exist → Prompts user to choose
- If only one exists → Auto-selects it
- If neither exists → Shows clear error message

### 2. Model Classes

#### `APIModel`
Wraps external API (Google Gemini) with:
- HTTP request handling
- Timeout management (30s)
- Error recovery
- Response parsing for Google Generative AI format

#### `LLM`
Main interface supporting both backends:
- `__init__(model_choice=None)` - Initialize with 'local', 'api', or auto-detect
- `generate_content(prompt, system_instruction)` - Generate JSON response
- Internal methods for detection, loading, and initialization

### 3. JSON Extraction (Fixed)

The `generate_content()` method now handles:

**Plain JSON:**
```
{"thought": "test", "tool": "ping"}
```

**Markdown JSON blocks:**
```
```json
{"thought": "test", "tool": "ping"}
```
```

**Generic markdown blocks:**
```
```
{"thought": "test", "tool": "ping"}
```
```

The extraction:
1. Detects `\`\`\`json` or `\`\`\`` markers
2. Extracts content between markers
3. Parses as JSON
4. Wraps non-JSON in `{"thought": "..."}` if needed

### 4. Usage

```python
from agent.llm import get_model_choice

# Get model (auto-detects or prompts)
llm = get_model_choice()

# Generate response (works with both backends)
response = llm.generate_content(
    prompt="scan network 10.0.0.0/8",
    system_instruction="You are a pentest AI..."
)

# Response is always valid JSON
import json
parsed = json.loads(response)
```

## Configuration

### `.env` File
```properties
API_KEY="your-api-key-here"
URL="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
```

### Available Google Gemini Models
- `gemini-2.5-flash` (recommended)
- `gemini-2.5-pro`
- `gemini-2.0-flash`
- `gemini-2.0-flash-lite`

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No models/credentials | FileNotFoundError with clear message |
| Invalid API key | Returns error JSON: `{"thought": "API Error: ..."}` |
| JSON parse failure | Wraps response in thought field |
| Network timeout | Returns error response after 30s |
| User cancels selection | RuntimeError |

## Integration with Agent

The agent uses `get_model_choice()` automatically:

```python
# In agent.py async_init()
self.llm = get_model_choice()  # Returns LLM instance
```

The rest of the codebase doesn't need changes - it works transparently with both backends.

## Testing

```bash
# Test JSON extraction
python3 test_json_fix.py

# Test with local model
python3 -c "from agent.llm import LLM; llm = LLM('local'); print(llm.generate_content('test'))"

# Test with API
python3 -c "from agent.llm import LLM; llm = LLM('api'); print(llm.generate_content('test'))"
```

## Benefits

✅ **Flexibility** - Switch between local and cloud models without code changes
✅ **Robustness** - Handles markdown-wrapped JSON from APIs
✅ **User-Friendly** - Clear prompts and error messages
✅ **Automatic** - Detects and selects resources intelligently
✅ **Backward Compatible** - Existing code continues to work
