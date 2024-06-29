# Django Explain Errors Middleware

This Django middleware captures errors and exceptions, sends them to OpenAI for explanation, and prints the explanation to stdout when debug mode is enabled. It uses an environment variable to securely manage the OpenAI API key.

## Features

- Captures Django errors and exceptions
- Uses OpenAI to explain the error
- Securely manages the OpenAI API key using environment variables

## Installation

1. **Clone the repository**:

   ```sh
   git clone https://github.com/yourusername/django-explain-errors-middleware.git
   cd django-explain-errors-middleware
   ```

2. **Install dependencies**:

   ```sh
   pip install openai python-dotenv
   ```

3. **Add the middleware to your Django project**:

   - Copy `explain_errors_middleware.py` to your Django project's root directory.

   - Open your `settings.py` file and add the middleware to the `MIDDLEWARE` list:

     ```python
     MIDDLEWARE = [
         ...
         'explain_errors_middleware.ExplainErrorsMiddleware',
         ...
     ]
     ```

4. **Set up environment variables**:

   - Create a `.env` file in your project’s root directory and add your OpenAI API key:

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

   The middleware will capture the error, send it to OpenAI for explanation, and print the explanation to stdout.

## Example

Here is an example of how to use the middleware in a Django project:

```python
# settings.py

DEBUG = True

MIDDLEWARE = [
    ...
    'explain_errors_middleware.ExplainErrorsMiddleware',
    ...
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
