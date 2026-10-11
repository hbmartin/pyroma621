"""Load shared Hypothesis profiles before pytest resolves command-line options."""

from hypothesis import settings

settings.register_profile("property", max_examples=100, deadline=None)
settings.register_profile("property-deep", parent=settings.get_profile("property"), max_examples=1000)
settings.load_profile("property")
