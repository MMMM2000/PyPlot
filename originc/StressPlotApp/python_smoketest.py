import sys, os, pathlib
print("python_smoketest.py running")
print("sys.executable:", sys.executable)
# Do NOT import originpro here; just prove Python executes.
pathlib.Path(__file__).with_name("smoke.ok").write_text("ok")
