from __future__ import annotations

import os


# Unit/regression tests are deterministic and offline. Dedicated provider tests
# inject an OSRM response and verify the road-matrix contract separately.
os.environ.setdefault("ROUTING_PROVIDER", "static")
