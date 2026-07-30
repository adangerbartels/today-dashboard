Run the suite from the repository root:

    python3 -m unittest discover -s tests -t .

No test dependencies — the standard library's `unittest` is all that's used.
Each source module routes its network calls through one or two functions, and the
tests replace those (see `support.py`) rather than mocking HTTP.
