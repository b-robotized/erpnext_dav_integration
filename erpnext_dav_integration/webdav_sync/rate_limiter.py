import threading
import time


class RateLimiter:
	def __init__(self, rate=5, per=1):
		"""
		rate = max requests
		per = seconds
		Example: 5 requests per 1 second
		"""
		self.rate = rate
		self.per = per
		self.allowance = rate
		self.last_check = time.time()
		self.lock = threading.Lock()

	def wait(self):
		with self.lock:
			current = time.time()
			time_passed = current - self.last_check
			self.last_check = current

			self.allowance += time_passed * (self.rate / self.per)

			if self.allowance > self.rate:
				self.allowance = self.rate

			if self.allowance < 1.0:
				sleep_time = (1.0 - self.allowance) * (self.per / self.rate)
				time.sleep(sleep_time)
				self.allowance = 0
			else:
				self.allowance -= 1.0
