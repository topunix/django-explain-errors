import threading
from unittest.mock import patch

from django.test import SimpleTestCase

from explain_errors.throttle import SlidingWindowThrottle


class SlidingWindowThrottleTest(SimpleTestCase):

    def test_allows_up_to_max_calls_then_denies(self):
        throttle = SlidingWindowThrottle(max_calls=3, window_seconds=60)

        self.assertTrue(throttle.allow())
        self.assertTrue(throttle.allow())
        self.assertTrue(throttle.allow())
        self.assertFalse(throttle.allow())

    def test_allows_again_after_window_expires(self):
        fake_time = [1000.0]

        with patch("explain_errors.throttle.time.monotonic", side_effect=lambda: fake_time[0]):
            throttle = SlidingWindowThrottle(max_calls=1, window_seconds=10)

            self.assertTrue(throttle.allow())
            self.assertFalse(throttle.allow())

            fake_time[0] += 10.1

            self.assertTrue(throttle.allow())

    def test_thread_safety_never_exceeds_limit(self):
        throttle = SlidingWindowThrottle(max_calls=10, window_seconds=60)
        results = []
        results_lock = threading.Lock()

        def worker():
            allowed = throttle.allow()
            with results_lock:
                results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 10)
