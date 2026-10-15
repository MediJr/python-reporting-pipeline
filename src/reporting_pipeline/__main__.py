"""Allow ``python -m reporting_pipeline``."""

import sys

from .cli import main

sys.exit(main())
