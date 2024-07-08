from openai import OpenAI
import os
import traceback
from dotenv import load_dotenv
from django.conf import settings
from django.http import JsonResponse


class ExplainErrorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_exception(request, e)
        return None

    def process_exception(self, request, exception):
        if settings.DEBUG:
            # Get the exception traceback
            tb = traceback.format_exc()

            # Load environment variables from .env file
            load_dotenv()
            # Get the OpenAI API key from environment variable
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                raise ValueError("OpenAI API key not found. Please set the OPENAI_API_KEY environment variable.")

            client = OpenAI(
                api_key=openai_api_key,
            )

            # Construct the prompt
            prompt = f"Explain the following Django error in simple terms:\n\n{tb}"

            try:
 		# Call OpenAI API
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=150
                )
                explanation = response.choices[0].message.content

                # Print the explanation to stdout
                print("Error Explanation by OpenAI:\n", explanation)

            except Exception as e:
                # In case of any issues with the OpenAI API call, print an error message
                print("Failed to get an explanation from OpenAI:", e)

            # Optionally, you can return a custom error response
            return JsonResponse({"error": "An error occurred.", "message": explanation}, status=500)
        return None
