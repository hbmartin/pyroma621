"""Optional profiles selected through pytest's Hypothesis plugin."""

from hypothesis import settings

settings.register_profile("property", max_examples=100, deadline=None)
settings.register_profile("property-deep", max_examples=1000, deadline=None)
