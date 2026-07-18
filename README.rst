# Django Explain Errors Middleware

This Django middleware captures errors and exceptions, sends them to OpenAI for explanation, and prints the explanation to stdout when debug mode is enabled. It can optionally ground explanations in your own project source code using a local vector index (RAG), so explanations reference the actual code that failed instead of staying generic.

The middleware supports both synchronous (WSGI) and asynchronous (ASGI) views. It auto-detects the view chain at startup and routes requests through the matching sync or async path, so no extra configuration is required to use it under either server type. Tracebacks are sanitized before leaving the process, and API calls are rate limited. It uses an environment variable to securely manage the OpenAI API key.

## Features

- Captures Django errors and exceptions
- Uses OpenAI to explain the error
- Optional codebase-aware explanations (RAG) backed by a local sqlite-vec index (see the RAG section below)
- Redacts secrets, tokens, and emails from tracebacks before sending
- Rate limits API calls with a configurable sliding window
- Works with both sync (WSGI) and async (ASGI) views
- Securely manages the OpenAI API key using environment variables

## Installation

1. Install django-explain-errors by running:
```bash
pip install django-explain-errors
```

2. **Add the middleware to your Django project**:

   - Open your `settings.py` file and add the middleware to the `MIDDLEWARE` list. Ensure that the middleware is added last in the list:

     ```python
     MIDDLEWARE = [
         ...
         'explain_errors.middleware.ExplainErrorsMiddleware',
     ]
     ```

3. **Set up environment variables**:

   - Create a `.env` file in your project's root directory and add your OpenAI API key. Alternatively, you can set the API key in `settings.py`:

     ```plaintext
     OPENAI_API_KEY=your_openai_api_key_here
     ```

## Usage

1. **Ensure DEBUG is set to True**:

   Open your `settings.py` file and set:

   ```python
   DEBUG = True
   ```

2. **Trigger an error in your Django application**:

   The middleware will capture the error, send it to OpenAI for explanation, and print the explanation to stdout. When an exception is caught, it returns a JSON `500` response containing the error message and the explanation.

## Async Support

The middleware exposes both `sync_capable = True` and `async_capable = True`. At initialization it inspects `get_response` to decide whether it is part of a sync or async chain:

- Under WSGI (for example `runserver` with sync views), requests flow through the synchronous handler.
- Under ASGI (for example with async views), requests are awaited through the async handler. The blocking OpenAI call is offloaded with `asgiref.sync.sync_to_async` so the event loop is not blocked.

No additional settings are needed. Place the middleware last in `MIDDLEWARE` for both modes.

## Configuration

| Setting / variable | Required | Description |
| ------------------ | -------- | ----------- |
| `OPENAI_API_KEY` (env or settings) | Yes, when `DEBUG=True` | API key used to authenticate with OpenAI. Read first from the environment, then from `settings`. |
| `DEBUG` | Yes | The middleware is only active when `DEBUG=True`. When `False`, requests pass through untouched. |
| `OPENAI_MODEL` | No | Model used for explanations. Defaults to `gpt-4o-mini`. |
| `OPENAI_MAX_TOKENS` | No | Maximum tokens in the explanation. Defaults to `150`. |
| `OPENAI_TIMEOUT` | No | Request timeout in seconds for the OpenAI client. Defaults to `10`. |
| `OPENAI_MAX_TRACEBACK_CHARS` | No | Traceback is trimmed to its last N characters before being sent. Defaults to `3000`. |

## Example

Here is an example of how to use the middleware in a Django project:

```python
# settings.py

DEBUG = True

MIDDLEWARE = [
    ...
    'explain_errors.middleware.ExplainErrorsMiddleware',
]

# .env

OPENAI_API_KEY=your_openai_api_key_here
```

When an error occurs, you will see an explanation printed to stdout.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## Acknowledgements

- [Django](https://www.djangoproject.com/)
- [OpenAI](https://www.openai.com/)
- [python-dotenv](https://github.com/theskumar/python-dotenv)
