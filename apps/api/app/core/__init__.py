import time

__version__ = "0.1.0"

# Captured at process import time (effectively process start) so uptime can
# be reported without any extra state or a running-services registry.
PROCESS_STARTED_AT = time.monotonic()
