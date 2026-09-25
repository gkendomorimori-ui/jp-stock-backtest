"""Technical indicators.

Rules for every indicator added here:
    * value at date T must depend only on data up to and including T (no look-ahead)
    * parameters are passed as arguments, never hard-coded
    * input/output are pandas Series/DataFrames indexed by date
    * each indicator has a unit test in ``tests/``
"""
