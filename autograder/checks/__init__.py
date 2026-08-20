"""Every check, grouped the way the guide is.

Importing this package is what fills the registry — each module registers its
checks as a side effect of being imported, so the runner imports the package and
then reads `registry.CHECKS`.
"""

from . import part1_host, part2_agent, part3_deploy, part4_access, part5_providers  # noqa: F401
