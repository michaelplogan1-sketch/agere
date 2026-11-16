"""The tests that read the synthetic semantic layer (about 8.8 GB, not in git) skip with this reason when it is absent, so a
fresh checkout runs every other test. See README.md, "First run", for how to get the layer."""
import unittest

from clinical_core.agere.tiered import store

HAVE_WAREHOUSE = store.path().is_file()
needs_warehouse = unittest.skipUnless(HAVE_WAREHOUSE, f'needs the semantic layer at {store.path()} (not in git; see README "First run")')
