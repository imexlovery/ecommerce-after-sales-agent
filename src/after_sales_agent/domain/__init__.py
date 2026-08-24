"""Project-owned business types for the logistics investigation domain.

Import concrete contracts from ``domain.models`` or ``domain.state``. Keeping
the package initializer side-effect free prevents a cycle with evidence types,
which are intentionally owned by the governed-tool boundary.
"""
