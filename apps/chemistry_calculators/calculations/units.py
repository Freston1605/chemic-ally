import pint

# pint is a library for handling physical quantities with units
# It allows easy conversion between units and provides a way to define custom
# units.

# This code snippet initializes a pint UnitRegistry,
# which is used to create and manage physical quantities with units.
# ``pint.UnitRegistry`` is the main entry point for creating quantities and
# performing unit conversions.

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity
