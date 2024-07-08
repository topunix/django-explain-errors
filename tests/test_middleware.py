import os
import unittest
from unittest.mock import patch, MagicMock
from django.test import TestCase, RequestFactory
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from custom_middleware import ExplainErrorsMiddleware
import django
from dotenv import load_dotenv



load_dotenv()

# Set up Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_project.settings')
django.setup()

class TestExplainErrorsMiddleware(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = ExplainErrorsMiddleware(self.get_response)
        settings.DEBUG = True

    def get_response(self, request):
        # Simulate a view that raises an exception
        raise ValueError("An example error")

    @patch('openai.Client.chat_completions.create')
    def test_process_exception(self, mock_chat_completions_create):
        # Mock the OpenAI API response
        mock_chat_completions_create.return_value = MagicMock(choices=[MagicMock(message={"content": "Mock explanation"})])

        # Create a request
        request = self.factory.get('/')

        # Process the request through the middleware
        response = self.middleware.process_exception(request, ValueError("An example error"))

        # Assert that the middleware captured the exception
        self.assertEqual(response.status_code, 500)
        self.assertIn("Mock explanation", response.content.decode())

        # Assert that the OpenAI API was called
        mock_chat_completions_create.assert_called_once()

    @patch('openai.Client.chat_completions.create')
    def test_no_exception(self, mock_chat_completions_create):
        # Mock the OpenAI API response
        mock_chat_completions_create.return_value = MagicMock(choices=[MagicMock(message={"content": "Mock explanation"})])

        # Define a new middleware with a response that does not raise an exception
        def get_response_no_exception(request):
            return HttpResponse("No error")

        middleware_no_exception = ExplainErrorsMiddleware(get_response_no_exception)

        # Create a request
        request = self.factory.get('/')

        # Process the request through the middleware
        response = middleware_no_exception(request)

        # Assert that the middleware did not capture an exception
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "No error")

        # Assert that the OpenAI API was not called
        mock_chat_completions_create.assert_not_called()

    @patch('openai.Client.chat_completions.create')
    def test_error_in_middleware(self, mock_chat_completions_create):
        # Mock the OpenAI API response
        mock_chat_completions_create.return_value = MagicMock(choices=[MagicMock(message={"content": "Mock explanation"})])

        # Create a new middleware that raises an exception
        class MiddlewareWithException(ExplainErrorsMiddleware):
            def __call__(self, request):
                raise ValueError("Middleware error")

        middleware_with_exception = MiddlewareWithException(self.get_response)

        # Create a request
        request = self.factory.get('/')

        # Process the request through the middleware and catch the exception
        try:
            middleware_with_exception(request)
        except ValueError as e:
            # Assert that the exception raised in middleware is not captured by itself
            self.assertEqual(str(e), "Middleware error")

if __name__ == '__main__':
    unittest.main()

