import json
from django.http import JsonResponse
from django.test import SimpleTestCase, RequestFactory
from explain_errors.middleware import ExplainErrorsMiddleware

class ExplainErrorsMiddlewareTest(SimpleTestCase):

  def test_process_exception_with_error(self):
    # Simulate an exception
    exception = Exception("Test Exception")

    # Create a mock request object using RequestFactory
    factory = RequestFactory()
    request = factory.get('/')

    # Create the middleware (no need for settings)
    middleware = ExplainErrorsMiddleware(lambda request: None)

    # Call process_exception
    response = middleware.process_exception(request, exception)

    self.assertIsInstance(response, JsonResponse)
    self.assertEqual(response.status_code, 500)
    response_json = json.loads(response.content)

    # Check if "error" key is in the response JSON
    self.assertIn("error", response_json)
