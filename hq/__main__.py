"""`python3 -m hq …` still works: it runs `python3 -m engine …` (RA-055 alias)."""
import runpy

runpy.run_module("engine", run_name="__main__", alter_sys=True)
