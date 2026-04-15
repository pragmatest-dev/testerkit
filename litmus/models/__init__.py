"""Pure Pydantic types for Litmus domain entities.

This package contains the domain data models — what a product, station,
capability, instrument, or catalog entry *is*. It intentionally has no
behavior, no I/O, and no runtime dependencies on other Litmus packages
that perform I/O or execute tests. Anything that does work (loading YAML,
running tests, rendering UI, serving HTTP) imports *from* this package and
is kept out of it.

This separation exists so that any module can import a domain type without
triggering heavier packages (`products`, `instruments`, `execution`, ...)
and the indirect dependency cycles they can create.
"""
